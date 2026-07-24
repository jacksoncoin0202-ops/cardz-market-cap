"use client";

import { Suspense, useState } from "react";
import { HeatmapTilesBoard } from "@/components/heatmap-tiles-board";
import { TunePanel } from "@/components/tune-panel";
import { DEFAULT_TILE, type TileParams } from "@/lib/tile-style";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { MarketCardView } from "@/lib/types";

/* 調參 lab：左邊全幅 heatmap、右邊固定 panel，專畀 testers 校完貼 JSON 返嚟 */
function TuneLabInner({ cards }: { cards: MarketCardView[] }) {
  const { period, theme } = useMarketSettings();
  const [params, setParams] = useState<TileParams>(DEFAULT_TILE);
  return (
    <div className="tune-lab">
      <div className="tune-lab-board">
        <HeatmapTilesBoard cards={cards} period={period} params={params} dark={theme === "dark"} />
      </div>
      <div className="tune-lab-side">
        <TunePanel params={params} onChange={setParams} dark={theme === "dark"} />
      </div>
    </div>
  );
}

export function TuneLab({ cards }: { cards: MarketCardView[] }) {
  return (
    <Suspense fallback={null}>
      <TuneLabInner cards={cards} />
    </Suspense>
  );
}
