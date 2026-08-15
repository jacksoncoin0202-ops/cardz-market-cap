"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CardImage } from "./card-image";
import { heatmapTreemapLayout } from "@/lib/ranked-strip-layout";
import { changeValue, tileColors, tileStyle, type TileParams } from "@/lib/tile-style";
import type { MarketCardView, MarketWindow } from "@/lib/types";

/* 純 tiles board：填滿父容器（100%×100%），冇 heading/controls，俾 tune lab 重用 */
export function HeatmapTilesBoard({ cards, period, params, dark, onPick }: {
  cards: MarketCardView[];
  period: MarketWindow;
  params: TileParams;
  dark: boolean;
  onPick?: (card: MarketCardView) => void;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(frame);
    const rect = frame.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) setSize({ width: rect.width, height: rect.height });
    return () => observer.disconnect();
  }, []);

  const tiles = useMemo(() => heatmapTreemapLayout(
    cards.map((card) => ({ card, rank: card.viewRank, value: Math.max(1, card.marketCap.value ?? 1) })),
    size.width,
    size.height,
  ), [cards, size.height, size.width]);

  const colors = tileColors(dark, params);

  return (
    <div className="heatmap-frame" ref={frameRef}>
      {tiles.map(({ item, x, y, width, height }) => {
        const card = item.card;
        const gap = params.gap;
        const tileX = x + gap / 2;
        const tileY = y + gap / 2;
        const tileW = width - gap;
        const tileH = height - gap;
        const st = tileStyle(changeValue(card, period), tileW, tileH, colors, params);
        return (
          <button
            className="heatmap-tile"
            key={card.id}
            type="button"
            data-dir={st.direction}
            style={{ left: tileX, top: tileY, width: tileW, height: tileH, background: st.bg }}
            aria-label={`#${card.viewRank} ${card.officialName ?? ""} ${card.collectorNumber}`.trim()}
            onClick={() => onPick?.(card)}
          >
            {st.showCard ? (
              <span
                className="tile-card"
                aria-hidden="true"
                style={{ width: st.cardW, height: st.cardH, left: (tileW - st.cardW) / 2, top: (tileH - st.cardH) / 2 }}
              >
                <CardImage image={card.image} sizes={`${Math.max(40, Math.round(st.cardW))}px`} loading={card.viewRank <= 8 ? "eager" : "lazy"} alt={card.officialName ?? ""} />
              </span>
            ) : null}
            {st.move ? (
              <span className="tile-move" style={{ fontSize: st.fontSize }} aria-hidden="true">{st.move}</span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
