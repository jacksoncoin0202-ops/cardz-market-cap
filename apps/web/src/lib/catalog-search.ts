import { intrinsicSize, locales, marketWindows, type CatalogEntry, type Locale, type LocalizedText, type MarketCardView, type MarketMetric, type MarketViewSnapshot, type SealedProductView, type WindowMetrics } from "./types";
import { foldSearchText, normaliseQuery } from "./list-explore";

export const CATALOG_LIST_CAP = 80;
export const CATALOG_HEADER_CAP = 8;

function slimLocales(text: Partial<Record<Locale, string | null>> | undefined): Partial<Record<Locale, string>> {
  if (!text) return {};
  const out: Partial<Record<Locale, string>> = {};
  for (const locale of locales) {
    const value = text[locale]?.trim();
    if (value) out[locale] = value;
  }
  return out;
}

/*
 * 索引只帶 200px 縮圖（榜頁 thumb 用），但 `width` / `height` 照原圖帶 ——
 * `<img width height>` 要嘅係比例，唔係位元組尺寸；`_200` 係同一張圖等比縮，
 * 比例一樣。冇尺寸就唔出（`intrinsicSize`）。
 */
function slimImage(image: CatalogEntry["image"]): CatalogEntry["image"] {
  const variant200 = image.variants?.["200"];
  return {
    url: variant200 || image.url,
    alt: image.alt,
    kind: image.kind,
    ...(variant200 ? { variants: { "200": variant200 } } : {}),
    ...intrinsicSize(image.width, image.height),
  };
}

function slimMetric(metric: MarketMetric<number>): MarketMetric<number> {
  const slim: MarketMetric<number> = {
    value: metric.value,
    status: metric.status,
    asOf: metric.asOf,
  };
  if (metric.sourceSwitched) slim.sourceSwitched = true;
  return slim;
}

function slimWindows(windows: MarketCardView["windows"]): MarketCardView["windows"] {
  return Object.fromEntries(Object.entries(windows).map(([period, metrics]) => [period, {
    changePct: slimMetric(metrics.changePct),
    marketCapChangePct: slimMetric(metrics.marketCapChangePct),
    trackedSalesChangePct: slimMetric(metrics.trackedSalesChangePct),
    trackedSales: {
      valueUsd: slimMetric(metrics.trackedSales.valueUsd),
      count: slimMetric(metrics.trackedSales.count),
      coverage: metrics.trackedSales.coverage,
      asOf: metrics.trackedSales.asOf,
    },
  }])) as MarketCardView["windows"];
}

function downsampleSparkline(values: number[] | undefined, maxPoints = 24): number[] {
  if (!values?.length) return [];
  if (values.length <= maxPoints) return values;
  const last = maxPoints - 1;
  return Array.from({ length: maxPoints }, (_, index) => {
    const source = index === last ? values.length - 1 : Math.round((index * (values.length - 1)) / last);
    return values[source];
  });
}

export function catalogEntryFromCard(card: MarketCardView): CatalogEntry {
  return {
    kind: "card",
    id: card.id,
    href: `/card/${card.id}`,
    officialName: card.officialName,
    name: slimLocales(card.name),
    collectorNumber: card.collectorNumber,
    setName: slimLocales(card.setName),
    setCode: card.printingIdentity?.setCode ?? null,
    tcg: card.tcg,
    cardLanguage: card.cardLanguage,
    marketRank: card.marketRank,
    image: slimImage({
      url: card.image.url,
      alt: card.image.alt,
      kind: card.image.kind,
      variants: card.image.variants,
      width: card.image.width,
      height: card.image.height,
    }),
    pricePsa10: slimMetric(card.pricePsa10),
    populationPsa10: slimMetric(card.populationPsa10),
    marketCap: slimMetric(card.marketCap),
    windows: slimWindows(card.windows),
    salesSparkline: downsampleSparkline(card.salesSparkline),
  };
}

export function catalogEntryFromBox(product: SealedProductView): CatalogEntry {
  return {
    kind: "box",
    id: product.id,
    href: `/box/${product.id}`,
    officialName: product.name.en,
    name: slimLocales(product.fullName ?? product.name),
    collectorNumber: product.setCode,
    setName: slimLocales(product.name),
    setCode: product.setCode,
    tcg: product.game === "ptcg" ? "Pokémon" : "One Piece",
    cardLanguage: product.lang === "jp" ? "ja" : "en",
    marketRank: product.rank,
    image: slimImage({
      url: product.image.url,
      alt: product.name.en,
      kind: product.image.kind === "placeholder" ? "placeholder" : "box_front",
      variants: product.image.variants,
      width: product.image.width,
      height: product.image.height,
    }),
  };
}

export function buildCatalogIndex(snapshot: Pick<MarketViewSnapshot, "top100" | "watchlist" | "sealed">): CatalogEntry[] {
  const seen = new Set<string>();
  const entries: CatalogEntry[] = [];
  for (const card of [...snapshot.top100, ...snapshot.watchlist]) {
    if (seen.has(`card:${card.id}`)) continue;
    seen.add(`card:${card.id}`);
    entries.push(catalogEntryFromCard(card));
  }
  for (const product of snapshot.sealed?.products ?? []) {
    if (seen.has(`box:${product.id}`)) continue;
    seen.add(`box:${product.id}`);
    entries.push(catalogEntryFromBox(product));
  }
  return entries;
}

export function displayCatalogName(entry: CatalogEntry, locale: Locale, fallback = ""): string {
  if (locale === "en") return entry.officialName || entry.name.en || fallback;
  return entry.name[locale] || entry.officialName || entry.name.en || fallback;
}

/*
 * 每個 entry 嘅 haystack 只砌一次（跟 object identity 記住，同 list-explore.ts `cachedHaystack` 同一模式）：
 * 逐粒字打搜尋會對成千個 entry 重跑 normalize。內容已經一次過收晒全部 locale 嘅 name／setName，
 * 唔會隨 locale 變，所以唔使再按 locale 分格。WeakMap 跟住 catalog payload 一齊回收。
 */
const haystackCache = new WeakMap<CatalogEntry, string>();

function catalogHaystack(entry: CatalogEntry): string {
  let haystack = haystackCache.get(entry);
  if (haystack === undefined) {
    haystack = foldSearchText([
      entry.officialName,
      ...locales.map((locale) => entry.name[locale]),
      entry.collectorNumber,
      ...locales.map((locale) => entry.setName[locale]),
      entry.setCode,
      entry.tcg,
    ].filter(Boolean).join(" "));
    haystackCache.set(entry, haystack);
  }
  return haystack;
}

export function catalogMatchesQuery(entry: CatalogEntry, query: string): boolean {
  const needle = normaliseQuery(query);
  if (!needle) return true;
  return catalogHaystack(entry).includes(needle);
}

/*
 * 細分排序：完整編號／全名優先，之後先至係 includes。
 * 同分用 marketRank（0 = 等緊新價，排最後）。
 */
export function catalogMatchScore(entry: CatalogEntry, query: string, locale: Locale): number {
  const needle = normaliseQuery(query);
  if (!needle) return entry.marketRank > 0 ? entry.marketRank : Number.MAX_SAFE_INTEGER;
  const number = foldSearchText(entry.collectorNumber ?? "");
  const name = foldSearchText(displayCatalogName(entry, locale));
  const official = foldSearchText(entry.officialName ?? "");
  if (number && number === needle) return 0;
  if (number.startsWith(needle)) return 1;
  if (name && name === needle) return 2;
  if (name.startsWith(needle)) return 3;
  if (official.startsWith(needle)) return 4;
  if (!catalogMatchesQuery(entry, query)) return Number.POSITIVE_INFINITY;
  const rank = entry.marketRank > 0 ? entry.marketRank : 50_000;
  return 10 + rank / 100_000;
}

export interface CatalogSearchOptions {
  kind?: CatalogEntry["kind"];
  tcg?: string;
  limit?: number;
}

/* 完整命中（已排序、未截）。limit 只喺出面截，唔准影響 total。 */
function rankCatalog(
  entries: readonly CatalogEntry[],
  query: string,
  locale: Locale,
  options?: CatalogSearchOptions,
): CatalogEntry[] {
  const needle = normaliseQuery(query);
  const pool = entries.filter((entry) => {
    if (options?.kind && entry.kind !== options.kind) return false;
    if (options?.tcg && entry.tcg !== options.tcg) return false;
    return true;
  });
  if (!needle) return pool;
  return pool
    .map((entry) => ({ entry, score: catalogMatchScore(entry, query, locale) }))
    .filter((item) => Number.isFinite(item.score))
    .sort((left, right) => {
      if (left.score !== right.score) return left.score - right.score;
      const rankA = left.entry.marketRank > 0 ? left.entry.marketRank : Number.MAX_SAFE_INTEGER;
      const rankB = right.entry.marketRank > 0 ? right.entry.marketRank : Number.MAX_SAFE_INTEGER;
      return rankA - rankB || left.entry.id.localeCompare(right.entry.id);
    })
    .map((item) => item.entry);
}

/*
 * `hits` = 截咗 limit 之後真係要 render 嗰批，`total` = 未截之前嘅命中數。
 * 「顯示 N／共 M」同「再顯示」要兩個數都攞得到，所以行呢個；
 * `searchCatalog` 保持舊 signature（只出 hits），現有 caller 唔使改。
 */
export function searchCatalogPage(
  entries: readonly CatalogEntry[],
  query: string,
  locale: Locale,
  options?: CatalogSearchOptions,
): { hits: CatalogEntry[]; total: number } {
  const hits = rankCatalog(entries, query, locale, options);
  const limit = options?.limit;
  /* limit 0 要真係出 0 行：舊寫法 `options?.limit ? …` 會當 0 係「冇上限」照出全部。 */
  const capped = typeof limit === "number" && Number.isFinite(limit) && limit >= 0;
  return { hits: capped ? hits.slice(0, limit) : hits, total: hits.length };
}

export function searchCatalog(
  entries: readonly CatalogEntry[],
  query: string,
  locale: Locale,
  options?: CatalogSearchOptions,
): CatalogEntry[] {
  return searchCatalogPage(entries, query, locale, options).hits;
}

const emptyMetric: MarketMetric<number> = { value: null, status: "unavailable", asOf: null };
const emptyWindow: WindowMetrics = {
  changePct: emptyMetric,
  marketCapChangePct: emptyMetric,
  trackedSalesChangePct: emptyMetric,
  trackedSales: { valueUsd: emptyMetric, count: emptyMetric, coverage: "unavailable", asOf: null },
};

function localizedFromPartial(partial: Partial<Record<Locale, string>>, fallback: string): LocalizedText {
  return {
    en: partial.en || fallback,
    "zh-TW": partial["zh-TW"] ?? null,
    "zh-CN": partial["zh-CN"] ?? null,
    ja: partial.ja ?? null,
    ko: partial.ko ?? null,
  };
}

/* 搜尋命中要行同一張榜表，所以 catalog 要還原成 list card 形狀。 */
export function catalogToCard(entry: CatalogEntry): MarketCardView | null {
  if (entry.kind !== "card" || !entry.pricePsa10 || !entry.populationPsa10 || !entry.marketCap) return null;
  const windows = { ...Object.fromEntries(marketWindows.map((period) => [period, emptyWindow])), ...entry.windows } as MarketCardView["windows"];
  const rank = entry.marketRank > 0 ? entry.marketRank : 0;
  return {
    id: entry.id,
    rank,
    marketRank: rank,
    viewRank: rank,
    tcg: entry.tcg,
    cardLanguage: entry.cardLanguage,
    printingIdentity: { setCode: entry.setCode, finishCode: null },
    collectorNumber: entry.collectorNumber,
    officialName: entry.officialName,
    name: localizedFromPartial(entry.name, entry.officialName ?? ""),
    setName: localizedFromPartial(entry.setName, ""),
    image: {
      url: entry.image.url,
      alt: entry.image.alt,
      kind: entry.image.kind === "raw_front" ? "raw_front" : "placeholder",
      variants: entry.image.variants,
      ...intrinsicSize(entry.image.width, entry.image.height),
    },
    pricePsa10: entry.pricePsa10,
    populationPsa10: entry.populationPsa10,
    marketCap: entry.marketCap,
    windows,
    historyDaily: [],
    salesSparkline: entry.salesSparkline ?? [],
  };
}
