import { graderSnapshot, loadMarketSnapshot, scopeSnapshot, singleCardSnapshot } from "@/lib/server-snapshot";
import type { Grader, MarketCardView, MarketViewSnapshot } from "@/lib/types";

/*
 * 前端統一數據入口。
 * 所有頁面同 API route 一律經呢度攞數據 — 後端接駁（R2 / D1 / 外部 API）
 * 只需要改 server-snapshot.ts 嘅 loadMarketSnapshot，上層契約不變。
 * 契約詳情見 docs/data-contract.md。
 */

export type MarketScope = "all" | "pokemon" | "one-piece" | "watchlist";

export interface CardListPayload {
  generatedAt: string;
  effectiveAt: string;
  count: number;
  cards: MarketCardView[];
}

function listPayload(snapshot: MarketViewSnapshot): CardListPayload {
  return {
    generatedAt: snapshot.generatedAt,
    effectiveAt: snapshot.effectiveAt,
    count: snapshot.top100.length,
    cards: snapshot.top100,
  };
}

export async function getMarketData(scope: MarketScope): Promise<CardListPayload> {
  return listPayload(scopeSnapshot(await loadMarketSnapshot(), scope));
}

export async function getCardData(id: string): Promise<MarketCardView | null> {
  const snapshot = singleCardSnapshot(await loadMarketSnapshot(), id);
  return snapshot.top100[0] ?? null;
}

export async function getGraderData(grader: Grader): Promise<CardListPayload> {
  return listPayload(graderSnapshot(await loadMarketSnapshot(), grader));
}
