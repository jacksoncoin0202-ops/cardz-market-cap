import { deriveBoxWindow, longWindows } from "./derive-windows";
import { marketWindows, sealedGroups, type MarketMetric, type SealedProductView, type SealedViewBlock } from "./types";

/*
 * BOX sidecar 投影。唔經 @cardz/market-data、唔經 PSA10 seed。
 * 公開路徑係 /box；呢度內部欄位名仍跟 export 嘅 sealed block。
 */

export interface BoxSidecarProduct {
  id: string;
  rank: number;
  game: "ptcg" | "optcg";
  lang: "en" | "jp";
  group: string;
  setCode: string;
  names: { en: string; jp?: string };
  fullNames?: { en: string; ja?: string };
  release: string | null;
  packsPerBox?: number;
  productKind: string;
  printWave: string;
  status: string;
  image?: { src: string; kind: string };
  story?: Partial<Record<"en" | "zh-TW" | "zh-CN" | "ja" | "ko", string>>;
  price?: {
    usd: number;
    kind: "sold" | "market" | "ask";
    asOf: string;
    native?: { amount: number; currency: string };
  } | null;
  askFloor?: {
    usd: number;
    asOf: string;
    native?: { amount: number; currency: string };
  };
  windows?: Partial<Record<"1d" | "7d" | "30d" | "90d" | "180d" | "365d", { changePct?: number; soldCount?: number }>>;
  historyDaily?: Array<{ date: string; priceUsd?: number | null; soldCount?: number; soldValueUsd?: number | null }>;
}

export interface BoxSidecarBlock {
  asOf: string;
  coverage?: { total: number; priced: number; imaged: number };
  products: BoxSidecarProduct[];
}

function boxLocalized(names: BoxSidecarProduct["names"]) {
  return {
    en: names.en,
    "zh-TW": null,
    "zh-CN": null,
    ja: names.jp || null,
    ko: null,
  };
}

function boxStory(story: BoxSidecarProduct["story"]): SealedProductView["story"] {
  if (!story || typeof story !== "object" || !story.en) return null;
  return {
    en: story.en,
    "zh-TW": story["zh-TW"] || null,
    "zh-CN": story["zh-CN"] || story["zh-TW"] || null,
    ja: story.ja || null,
    ko: story.ko || null,
  };
}

function boxProductView(product: BoxSidecarProduct): SealedProductView | null {
  if (product.status === "no-box") return null;
  if (!sealedGroups.includes(product.group as (typeof sealedGroups)[number])) return null;
  const imageIsSafe = Boolean(
    product.image
    && product.image.kind === "box_front"
    && /^\/market-assets\/[a-f0-9]{64}\.webp$/.test(product.image.src),
  );
  const price = product.price ?? null;
  const priceMetric: MarketMetric<number> = price
    ? { value: price.usd, status: "ready", asOf: price.asOf }
    : { value: null, status: "unavailable", asOf: null };
  const askMetric: MarketMetric<number> = product.askFloor
    ? { value: product.askFloor.usd, status: "ready", asOf: product.askFloor.asOf }
    : { value: null, status: "unavailable", asOf: null };
  const history = product.historyDaily || [];
  const windows = Object.fromEntries(marketWindows.map((window) => {
    if ((longWindows as readonly string[]).includes(window)) {
      const baked = product.windows?.[window];
      if (baked && typeof baked.changePct === "number") {
        return [window, {
          changePct: { value: baked.changePct, status: "ready" as const, asOf: price?.asOf ?? null },
          soldCount: baked.soldCount ?? 0,
        }];
      }
      return [window, deriveBoxWindow(history, price?.usd ?? null, price?.asOf ?? null, window as typeof longWindows[number])];
    }
    const metrics = product.windows?.[window];
    const changePct: MarketMetric<number> =
      metrics && typeof metrics.changePct === "number"
        ? { value: metrics.changePct, status: "ready", asOf: price?.asOf ?? null }
        : { value: null, status: "accumulating", asOf: null };
    return [window, { changePct, soldCount: metrics?.soldCount ?? 0 }];
  })) as SealedProductView["windows"];
  return {
    id: product.id,
    rank: product.rank,
    game: product.game,
    lang: product.lang,
    group: product.group as SealedProductView["group"],
    setCode: product.setCode,
    name: boxLocalized(product.names),
    fullName: product.fullNames?.en
      ? {
        en: product.fullNames.en,
        "zh-TW": null,
        "zh-CN": null,
        ja: product.fullNames.ja || null,
        ko: null,
      }
      : null,
    release: product.release ?? null,
    packsPerBox: product.packsPerBox ?? 0,
    productKind: product.productKind,
    printWave: product.printWave,
    status: product.status === "unreleased" ? "unreleased" : "active",
    image: {
      url: imageIsSafe && product.image ? product.image.src : "/card-placeholder.svg",
      kind: imageIsSafe ? "box_front" : "placeholder",
      variants: imageIsSafe && product.image
        ? {
          "200": product.image.src.replace(/\.webp$/, "_200.webp"),
          "600": product.image.src.replace(/\.webp$/, "_600.webp"),
        }
        : undefined,
    },
    story: boxStory(product.story),
    priceUsd: priceMetric,
    priceKind: price?.kind ?? null,
    priceNative: price?.native ?? null,
    askFloorUsd: askMetric,
    askFloorNative: product.askFloor?.native ?? null,
    windows,
    historyDaily: (product.historyDaily || []).map((point) => ({
      at: point.date,
      priceUsd: typeof point.priceUsd === "number" ? point.priceUsd : null,
      priceStatus: typeof point.priceUsd === "number" ? "ready" : "accumulating",
      trackedSalesValueUsd: typeof point.soldValueUsd === "number" ? point.soldValueUsd : null,
      trackedSalesCount: typeof point.soldCount === "number" ? point.soldCount : null,
      salesCoverage: "partial" as const,
      salesVerifiedZero: point.soldCount === 0,
    })),
    salesSparkline: (product.historyDaily || [])
      .map((point) => point.soldValueUsd)
      .filter((value): value is number => typeof value === "number" && Number.isFinite(value)),
  };
}

export function boxBlockView(block: BoxSidecarBlock | null | undefined): SealedViewBlock | undefined {
  if (!block || !Array.isArray(block.products)) return undefined;
  const products = block.products
    .map(boxProductView)
    .filter((product): product is SealedProductView => product !== null);
  if (!products.length) return undefined;
  return {
    asOf: block.asOf,
    coverage: {
      total: block.coverage?.total ?? products.length,
      priced: block.coverage?.priced ?? 0,
      imaged: block.coverage?.imaged ?? 0,
    },
    products,
  };
}
