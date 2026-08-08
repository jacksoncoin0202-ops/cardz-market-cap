import { loadMarketSnapshot, scopeSnapshot, singleCardSnapshot, type ScopeOptions } from "@/lib/server-snapshot";
import type { MarketCardView, MarketViewSnapshot } from "@/lib/types";

/*
 * 前端統一數據入口。
 * 所有頁面同 API route 一律經呢度攞 immutable product snapshot。
 * Node / R2 transport 只由 server-snapshot.ts 嘅 loadMarketSnapshot 負責。
 * Public market data is read from the baked snapshot only.
 */

export type MarketScope = "all" | "pokemon" | "one-piece" | "watchlist";

export interface CardListPayload {
  generation: {
    id: string;
  };
  generatedAt: string;
  effectiveAt: string;
  coverage: MarketViewSnapshot["coverage"];
  count: number;
  cards: MarketCardView[];
}

function listPayload(snapshot: MarketViewSnapshot): CardListPayload {
  return {
    generation: {
      id: snapshot.generation,
    },
    generatedAt: snapshot.generatedAt,
    effectiveAt: snapshot.effectiveAt,
    coverage: snapshot.coverage,
    count: snapshot.top100.length,
    cards: snapshot.top100,
  };
}

export async function getMarketData(scope: MarketScope, options?: ScopeOptions): Promise<CardListPayload> {
  return listPayload(scopeSnapshot(await loadMarketSnapshot(), scope, options));
}

export async function getCardData(id: string): Promise<MarketCardView | null> {
  const snapshot = singleCardSnapshot(await loadMarketSnapshot(), id);
  return snapshot.top100[0] ?? null;
}
