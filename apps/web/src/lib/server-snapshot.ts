import { getCloudflareContext } from "@opennextjs/cloudflare";
import { assertPublicSnapshot, type PublicMarketSnapshot } from "@cardz/market-data";
import { cache } from "react";
import { getSeedSnapshot, normaliseSnapshot } from "./snapshot";
import { parseSnapshotPointer } from "./snapshot-pointer";
import type { Grader, MarketCardView, MarketViewSnapshot } from "./types";

interface R2Body {
  text(): Promise<string>;
}

interface MarketBucket {
  get(key: string): Promise<R2Body | null>;
}

let runtimeLastGood: MarketViewSnapshot | null = null;

function ranked(cards: MarketCardView[]): MarketCardView[] {
  return [...cards]
    .sort((a, b) => (b.marketCap.value ?? 0) - (a.marketCap.value ?? 0))
    .slice(0, 100)
    .map((card, index) => ({ ...card, rank: index + 1 }));
}

function listCard(card: MarketCardView): MarketCardView {
  return {
    ...card,
    story: { en: "", "zh-TW": "", "zh-CN": "", ja: "" },
    historyDaily: [],
  };
}

export const loadMarketSnapshot = cache(async (): Promise<MarketViewSnapshot> => {
  if (process.env.NODE_ENV === "development") return getSeedSnapshot();
  let allowDemo = process.env.MARKET_DATA_ALLOW_DEMO === "true";
  try {
    const context = await getCloudflareContext({ async: true });
    const environment = context.env as unknown as {
      MARKET_DATA?: MarketBucket;
      MARKET_DATA_ALLOW_DEMO?: string;
      MARKET_DATA_POINTER_KEY?: string;
    };
    allowDemo = environment.MARKET_DATA_ALLOW_DEMO === "true" || allowDemo;
    const bucket = environment.MARKET_DATA;
    if (!bucket) throw new Error("Market data binding unavailable");
    const pointerKey = environment.MARKET_DATA_POINTER_KEY ?? process.env.MARKET_DATA_POINTER_KEY ?? "latest.json";
    const pointerObject = await bucket.get(pointerKey);
    if (!pointerObject) throw new Error("Snapshot pointer unavailable");
    const pointer = parseSnapshotPointer(JSON.parse(await pointerObject.text()));
    const snapshotObject = await bucket.get(pointer.snapshotKey);
    if (!snapshotObject) throw new Error("Snapshot generation unavailable");
    const serialized = await snapshotObject.text();
    const canonical = JSON.parse(serialized) as PublicMarketSnapshot;
    assertPublicSnapshot(canonical, { production: !allowDemo });
    if (canonical.generation.id !== pointer.generationId || canonical.generation.contentSha256 !== pointer.sha256) {
      throw new Error("Snapshot generation does not match latest pointer");
    }
    const snapshot = normaliseSnapshot(canonical);
    if (canonical.generation.mode === "production" && canonical.generation.productionEligible) runtimeLastGood = snapshot;
    return snapshot;
  } catch (error) {
    if (runtimeLastGood) return runtimeLastGood;
    if (allowDemo) return getSeedSnapshot();
    if (error instanceof Error) throw error;
    throw new Error("Market snapshot unavailable");
  }
});

export function scopeSnapshot(
  snapshot: MarketViewSnapshot,
  scope: "all" | "pokemon" | "one-piece" | "watchlist",
): MarketViewSnapshot {
  if (scope === "all") return { ...snapshot, top100: snapshot.top100.map(listCard), watchlist: [] };
  if (scope === "watchlist") return { ...snapshot, top100: snapshot.watchlist.map(listCard), watchlist: [] };
  const expected = scope === "pokemon" ? "Pokémon" : "One Piece";
  const candidates = [...snapshot.top100, ...snapshot.watchlist].filter((card) => card.tcg === expected);
  return { ...snapshot, top100: ranked(candidates).map(listCard), watchlist: [] };
}

export function singleCardSnapshot(snapshot: MarketViewSnapshot, id: string): MarketViewSnapshot {
  const card = [...snapshot.top100, ...snapshot.watchlist].find((candidate) => candidate.id === id);
  return { ...snapshot, top100: card ? [card] : [], watchlist: [] };
}

export function graderSnapshot(snapshot: MarketViewSnapshot, grader: Grader): MarketViewSnapshot {
  const cards = [...snapshot.top100, ...snapshot.watchlist]
    .filter((card) => card.graderPopulations[grader].topGradePopulation.value !== null)
    .sort((a, b) => (b.graderPopulations[grader].topGradePopulation.value ?? 0) - (a.graderPopulations[grader].topGradePopulation.value ?? 0))
    .slice(0, 100)
    .map((card, index) => ({ ...card, rank: index + 1 }));
  return { ...snapshot, top100: cards.map(listCard), watchlist: [] };
}
