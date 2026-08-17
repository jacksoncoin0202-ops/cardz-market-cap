"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { srcSet as cardSrcSet } from "./card-image";
import { HeatmapTile, tileFetchPriority, tileImageSizes } from "./heatmap-tile";
import { displayCardName } from "@/lib/card-name";
import { snapCardBox, snapFrameGrid, snapTileBox } from "@/lib/pixel-snap";
import { heatmapTreemapLayout } from "@/lib/ranked-strip-layout";
import { changeValue, tileColors, tileStyle, type TileParams } from "@/lib/tile-style";
import { useUpDown } from "@/lib/use-updown";
import type { Locale, MarketCardView, MarketWindow } from "@/lib/types";

/* 純 tiles board：填滿父容器（100%×100%），冇 heading/controls，俾 tune lab 重用 */
export function HeatmapTilesBoard({ cards, period, params, dark, locale, onPick }: {
  cards: MarketCardView[];
  period: MarketWindow;
  params: TileParams;
  dark: boolean;
  /* alt 要本地化卡名（同 `heatmap.tsx` 一樣行 `displayCardName`）。唔傳就當 en。 */
  locale?: Locale;
  onPick?: (card: MarketCardView) => void;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0, dpr: 1, originX: 0, originY: 0 });

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    /* 同主 heatmap 一樣：釘落絕對 device px 格（要 frame 嘅 viewport 位置，見 pixel-snap.ts）
       + 冇變就唔 set（sub-pixel 抖動唔好觸發成版 100 格重排） */
    const measure = (rect: DOMRect) => {
      const next = snapFrameGrid(rect.width, rect.height, window.devicePixelRatio || 1, rect.left, rect.top);
      setSize((prev) => (
        prev.width === next.width && prev.height === next.height && prev.dpr === next.dpr
          && prev.originX === next.originX && prev.originY === next.originY ? prev : next
      ));
    };
    const observer = new ResizeObserver(() => measure(frame.getBoundingClientRect()));
    observer.observe(frame);
    const rect = frame.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) measure(rect);
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
        /* 同主 heatmap 同一套釘格（lib/pixel-snap.ts）：gap 一律相等、卡圖落整數格 */
        const box = snapTileBox(x, y, width, height, params.gap, size);
        const st = tileStyle(changeValue(card, period), box.w, box.h, colors, params);
        const cardBox = snapCardBox(box.w, box.h, st.cardW, st.cardH, size.dpr);
        return (
          <HeatmapTile
            key={card.id}
            cardId={card.id}
            x={box.x}
            y={box.y}
            w={box.w}
            h={box.h}
            bg={st.bg}
            plate={st.plate}
            direction={st.direction}
            cardW={cardBox.cardW}
            cardH={cardBox.cardH}
            showCard={st.showCard}
            move={st.move}
            fontSize={st.fontSize}
            delay={0}
            late={false}
            imageSrc={card.image.url}
            imageSrcSet={cardSrcSet(card.image)}
            sizes={tileImageSizes(cardBox.cardW)}
            fetchPriority={tileFetchPriority(card.viewRank, cardBox.cardW)}
            alt={displayCardName(card, locale ?? "en")}
            ariaLabel={`#${card.viewRank} ${displayCardName(card, locale ?? "en")} ${card.collectorNumber}`.trim()}
            onPick={onPick ? handlePick : undefined}
          />
        );
      })}
    </div>
  );
}
