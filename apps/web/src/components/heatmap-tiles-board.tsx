"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { srcSet as cardSrcSet } from "./card-image";
import { HeatmapTile, tileFetchPriority, tileImageSizes } from "./heatmap-tile";
import { heatmapTreemapLayout } from "@/lib/ranked-strip-layout";
import { changeValue, tileColors, tileStyle, type TileParams } from "@/lib/tile-style";
import { useUpDown } from "@/lib/use-updown";
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
    /* 取整 + 冇變就唔 set：sub-pixel 抖動唔好觸發成版 100 格重排 */
    const measure = (width: number, height: number) => {
      const w = Math.round(width), h = Math.round(height);
      setSize((prev) => (prev.width === w && prev.height === h ? prev : { width: w, height: h }));
    };
    const observer = new ResizeObserver(([entry]) => measure(entry.contentRect.width, entry.contentRect.height));
    observer.observe(frame);
    const rect = frame.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) measure(rect.width, rect.height);
    return () => observer.disconnect();
  }, []);

  const tiles = useMemo(() => heatmapTreemapLayout(
    cards.map((card) => ({ card, rank: card.viewRank, value: Math.max(1, card.marketCap.value ?? 1) })),
    size.width,
    size.height,
  ), [cards, size.height, size.width]);

  /* 同主 heatmap 一樣：用戶揀咗紅升就對調 up/down 兩組色（F13 全站生效，/tune 板都跟） */
  const { resolved: upDown } = useUpDown();
  const colors = useMemo(() => {
    const base = tileColors(dark, params);
    return upDown === "red-up" ? { ...base, up: base.down, down: base.up } : base;
  }, [dark, params, upDown]);
  const cardsById = useMemo(() => new Map(cards.map((card) => [card.id, card])), [cards]);
  const onPickRef = useRef(onPick);
  useEffect(() => { onPickRef.current = onPick; }, [onPick]);
  const handlePick = useCallback((cardId: string) => {
    const card = cardsById.get(cardId);
    if (card) onPickRef.current?.(card);
  }, [cardsById]);

  return (
    <div className="heatmap-frame" ref={frameRef}>
      {tiles.map(({ item, x, y, width, height }) => {
        const card = item.card;
        const gap = params.gap;
        const tileW = width - gap;
        const tileH = height - gap;
        const st = tileStyle(changeValue(card, period), tileW, tileH, colors, params);
        return (
          <HeatmapTile
            key={card.id}
            cardId={card.id}
            x={x + gap / 2}
            y={y + gap / 2}
            w={tileW}
            h={tileH}
            bg={st.bg}
            direction={st.direction}
            cardW={st.cardW}
            cardH={st.cardH}
            showCard={st.showCard}
            move={st.move}
            fontSize={st.fontSize}
            delay={0}
            late={false}
            imageSrc={card.image.url}
            imageSrcSet={cardSrcSet(card.image)}
            sizes={tileImageSizes(st.cardW)}
            fetchPriority={tileFetchPriority(card.viewRank, st.cardW)}
            alt={card.officialName ?? ""}
            ariaLabel={`#${card.viewRank} ${card.officialName ?? ""} ${card.collectorNumber}`.trim()}
            onPick={onPick ? handlePick : undefined}
          />
        );
      })}
    </div>
  );
}
