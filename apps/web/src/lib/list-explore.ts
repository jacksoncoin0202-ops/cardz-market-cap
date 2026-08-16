import { defaultMarketWindow, type Locale, type MarketCardView, type MarketWindow, type SealedProductView } from "./types";

export const cardSortKeys = ["rank", "price", "pop", "sales", "change"] as const;
export const boxSortKeys = ["rank", "price", "sold", "release"] as const;
export const sortDirs = ["asc", "desc"] as const;

export type CardSortKey = (typeof cardSortKeys)[number];
export type BoxSortKey = (typeof boxSortKeys)[number];
export type SortDir = (typeof sortDirs)[number];

export const DEFAULT_SORT = "rank";
export const DEFAULT_DIR: SortDir = "desc";

/*
 * 搜尋文字折疊：NFKC（全形／半形假名、全形英數同一）→ 小寫 → NFD 拆音標 → 掉 \p{Diacritic}，
 * 所以 "pokemon" 搵到 "Pokémon"、"ﾋﾟｶﾁｭｳ" 搵到 "ピカチュウ"。needle 同 haystack 行同一條規矩。
 */
export function foldSearchText(value: string): string {
  return value.normalize("NFKC").toLowerCase().normalize("NFD").replace(/\p{Diacritic}/gu, "");
}

export function normaliseQuery(value: string | null | undefined): string {
  return foldSearchText((value ?? "").trim());
}

export function normaliseCardSort(value: string | null | undefined): CardSortKey {
  /* 舊 URL `?sort=cap`：市值序就係榜序，唔再開第二個互相取消嘅掣。 */
  if (value === "cap") return DEFAULT_SORT;
  return cardSortKeys.includes(value as CardSortKey) ? value as CardSortKey : DEFAULT_SORT;
}

export function normaliseBoxSort(value: string | null | undefined): BoxSortKey {
  return boxSortKeys.includes(value as BoxSortKey) ? value as BoxSortKey : DEFAULT_SORT;
}

export function normaliseSortDir(value: string | null | undefined): SortDir {
  return value === "asc" ? "asc" : DEFAULT_DIR;
}

export function nextExploreSort(currentKey: string, currentDir: SortDir, clicked: string): { sort: string; dir: SortDir } {
  if (clicked === DEFAULT_SORT) return { sort: DEFAULT_SORT, dir: DEFAULT_DIR };
  if (currentKey === clicked) return { sort: clicked, dir: currentDir === "desc" ? "asc" : "desc" };
  return { sort: clicked, dir: DEFAULT_DIR };
}

/*
 * 每張卡／每個原盒嘅 haystack 只砌一次（按 object identity + locale 記住）：
 * 逐粒字打搜尋會對成千行重跑 normalize，WeakMap 跟住 snapshot object 一齊回收。
 */
const haystackCache = new WeakMap<object, Partial<Record<Locale, string>>>();

function cachedHaystack(item: object, locale: Locale, build: () => Array<string | null | undefined>): string {
  let byLocale = haystackCache.get(item);
  if (!byLocale) {
    byLocale = {};
    haystackCache.set(item, byLocale);
  }
  let haystack = byLocale[locale];
  if (haystack === undefined) {
    haystack = foldSearchText(build().filter(Boolean).join(" "));
    byLocale[locale] = haystack;
  }
  return haystack;
}

export function cardMatchesQuery(card: MarketCardView, query: string, locale: Locale): boolean {
  const needle = normaliseQuery(query);
  if (!needle) return true;
  return cachedHaystack(card, locale, () => [
    card.officialName,
    card.name?.[locale],
    card.collectorNumber,
    card.setName[locale],
    card.setName.en,
  ]).includes(needle);
}

export function boxMatchesQuery(product: SealedProductView, query: string, locale: Locale): boolean {
  const needle = normaliseQuery(query);
  if (!needle) return true;
  return cachedHaystack(product, locale, () => [
    product.name[locale],
    product.name.en,
    product.fullName?.[locale],
    product.fullName?.en,
    product.setCode,
  ]).includes(needle);
}

function metricValue(metric: { value: number | null } | undefined): number | null {
  const value = metric?.value;
  return value === null || value === undefined || !Number.isFinite(value) ? null : value;
}

/* 打和用嘅 rank：viewRank 行先，rank 0（等緊新價）當最大排最後，唔准因為係 0 而搶到最前。 */
function tieBreakRank(card: MarketCardView): number {
  const rank = card.viewRank > 0 ? card.viewRank : card.marketRank;
  return rank > 0 ? rank : Number.MAX_SAFE_INTEGER;
}

/* 缺值排最後，唔當 0。相同數用原本 rank 打和，唔重新編號。 */
export function compareNullable(a: number | null, b: number | null, dir: SortDir): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return dir === "asc" ? a - b : b - a;
}

export function sortCards(
  cards: MarketCardView[],
  sort: CardSortKey,
  dir: SortDir,
  period: MarketWindow = defaultMarketWindow,
): MarketCardView[] {
  /* rank = 市值榜序。#1 永遠在最前；唔同 cap 再打一次仗。 */
  if (sort === "rank") {
    return cards.slice().sort((left, right) => {
      const rankA = left.marketRank > 0 ? left.marketRank : Number.MAX_SAFE_INTEGER;
      const rankB = right.marketRank > 0 ? right.marketRank : Number.MAX_SAFE_INTEGER;
      return rankA - rankB || left.id.localeCompare(right.id);
    });
  }
  return cards.slice().sort((left, right) => {
    const key = sort === "price"
      ? compareNullable(metricValue(left.pricePsa10), metricValue(right.pricePsa10), dir)
      : sort === "pop"
        ? compareNullable(metricValue(left.populationPsa10), metricValue(right.populationPsa10), dir)
        : sort === "sales"
          ? compareNullable(metricValue(left.windows[period]?.trackedSales.valueUsd), metricValue(right.windows[period]?.trackedSales.valueUsd), dir)
          : sort === "change"
            ? compareNullable(metricValue(left.windows[period]?.changePct), metricValue(right.windows[period]?.changePct), dir)
            : compareNullable(metricValue(left.marketCap), metricValue(right.marketCap), dir);
    if (key !== 0) return key;
    return tieBreakRank(left) - tieBreakRank(right);
  });
}

export function sortBoxes(
  products: SealedProductView[],
  sort: BoxSortKey,
  dir: SortDir,
  period: MarketWindow,
): SealedProductView[] {
  if (sort === "rank") return products.slice();
  return products.slice().sort((left, right) => {
    let key = 0;
    if (sort === "price") {
      key = compareNullable(metricValue(left.priceUsd), metricValue(right.priceUsd), dir);
    } else if (sort === "sold") {
      const leftSold = left.windows[period]?.soldCount ?? 0;
      const rightSold = right.windows[period]?.soldCount ?? 0;
      key = dir === "asc" ? leftSold - rightSold : rightSold - leftSold;
    } else {
      const leftRelease = left.release || null;
      const rightRelease = right.release || null;
      if (leftRelease === null && rightRelease === null) key = 0;
      else if (leftRelease === null) key = 1;
      else if (rightRelease === null) key = -1;
      else key = dir === "asc" ? leftRelease.localeCompare(rightRelease) : rightRelease.localeCompare(leftRelease);
    }
    if (key !== 0) return key;
    return left.rank - right.rank;
  });
}
