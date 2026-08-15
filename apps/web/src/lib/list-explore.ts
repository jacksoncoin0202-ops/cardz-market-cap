import type { Locale, MarketCardView, MarketWindow, SealedProductView } from "./types";

export const cardSortKeys = ["rank", "price", "pop", "cap"] as const;
export const boxSortKeys = ["rank", "price", "sold", "release"] as const;
export const sortDirs = ["asc", "desc"] as const;

export type CardSortKey = (typeof cardSortKeys)[number];
export type BoxSortKey = (typeof boxSortKeys)[number];
export type SortDir = (typeof sortDirs)[number];

export const DEFAULT_SORT = "rank";
export const DEFAULT_DIR: SortDir = "desc";

export function normaliseQuery(value: string | null | undefined): string {
  return (value ?? "").trim().toLowerCase();
}

export function normaliseCardSort(value: string | null | undefined): CardSortKey {
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

function textHas(query: string, value: string | null | undefined): boolean {
  return Boolean(value && value.toLowerCase().includes(query));
}

export function cardMatchesQuery(card: MarketCardView, query: string, locale: Locale): boolean {
  const needle = normaliseQuery(query);
  if (!needle) return true;
  return textHas(needle, card.officialName)
    || textHas(needle, card.collectorNumber)
    || textHas(needle, card.setName[locale])
    || textHas(needle, card.setName.en);
}

export function boxMatchesQuery(product: SealedProductView, query: string, locale: Locale): boolean {
  const needle = normaliseQuery(query);
  if (!needle) return true;
  return textHas(needle, product.name[locale])
    || textHas(needle, product.name.en)
    || textHas(needle, product.fullName?.[locale])
    || textHas(needle, product.fullName?.en)
    || textHas(needle, product.setCode);
}

function metricValue(metric: { value: number | null } | undefined): number | null {
  const value = metric?.value;
  return value === null || value === undefined || !Number.isFinite(value) ? null : value;
}

/* 缺值排最後，唔當 0。相同數用原本 rank 打和，唔重新編號。 */
export function compareNullable(a: number | null, b: number | null, dir: SortDir): number {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  return dir === "asc" ? a - b : b - a;
}

export function sortCards(cards: MarketCardView[], sort: CardSortKey, dir: SortDir): MarketCardView[] {
  if (sort === "rank") return cards.slice();
  return cards.slice().sort((left, right) => {
    const key = sort === "price"
      ? compareNullable(metricValue(left.pricePsa10), metricValue(right.pricePsa10), dir)
      : sort === "pop"
        ? compareNullable(metricValue(left.populationPsa10), metricValue(right.populationPsa10), dir)
        : compareNullable(metricValue(left.marketCap), metricValue(right.marketCap), dir);
    if (key !== 0) return key;
    return (left.viewRank || left.marketRank) - (right.viewRank || right.marketRank);
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
