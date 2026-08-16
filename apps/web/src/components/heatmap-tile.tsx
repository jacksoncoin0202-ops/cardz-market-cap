"use client";

import { memo, type AnimationEvent, type CSSProperties } from "react";
import { CardImg } from "./card-image";
import type { TileStyle } from "@/lib/tile-style";

/* 一格 heatmap tile（heatmap.tsx 同 /tune 嘅 HeatmapTilesBoard 共用）。
   全部 props 都係 primitive（或者 parent 用 useCallback 釘死嘅 handler），
   memo 淺比較就夠：hover / preview / sheet 呢啲 state 唔經 tile props，
   掃過一格唔會令其餘 99 格重 render；拉 slider 時先會因為幾何變咗而重畫。 */
export interface HeatmapTileProps {
  cardId: string;
  x: number;
  y: number;
  w: number;
  h: number;
  bg: string;
  direction: TileStyle["direction"];
  cardW: number;
  cardH: number;
  showCard: boolean;
  move: string | null;
  fontSize: number;
  /* 入場 stagger（ms）；拖 slider 加出嚟嘅 late tile 一律 0 */
  delay: number;
  late: boolean;
  imageSrc: string;
  imageSrcSet: string | undefined;
  sizes: string;
  fetchPriority: "high" | "low";
  alt: string;
  ariaLabel: string;
  onHover?: (cardId: string, el: HTMLButtonElement, x: number, y: number, w: number, h: number) => void;
  onFocus?: (cardId: string) => void;
  onPick?: (cardId: string, el: HTMLButtonElement) => void;
}

/* 卡圖幾何走 CSS var（--card-w/--card-h 寫喺 tile 上），.tile-card 自己嘅 inline
   style 係一個 module-level 常數 → 同一個 reference，React 連 diff 都慳返，
   拉 slider 每格由 10 個 style 寫入減到 6 個。同一組 rule 亦會落 globals.css，
   落咗之後呢個常數可以刪。 */
const TILE_CARD_STYLE: CSSProperties = {
  width: "var(--card-w)",
  height: "var(--card-h)",
  left: "50%",
  top: "50%",
  transform: "translate(-50%, -50%)",
};

/* pop / fade 播完就落旗：data-late 拆走（will-change 同 art gate 唔好長期 arm 住），
   data-settled 俾 CSS 停 animation。data-late 由 React 派但只喺 mount 寫一次，
   之後 props 冇變 React 唔會再補返，DOM 直接拆係安全嘅。 */
function settleTile(event: AnimationEvent<HTMLButtonElement>): void {
  if (event.target !== event.currentTarget) return;
  const el = event.currentTarget;
  el.removeAttribute("data-late");
  el.setAttribute("data-settled", "");
}

export const HeatmapTile = memo(function HeatmapTile(p: HeatmapTileProps) {
  return (
    <button
      className="heatmap-tile"
      type="button"
      data-dir={p.direction}
      data-late={p.late ? "" : undefined}
      style={{
        left: p.x,
        top: p.y,
        width: p.w,
        height: p.h,
        background: p.bg,
        "--d": `${p.delay}ms`,
        "--card-w": `${p.cardW}px`,
        "--card-h": `${p.cardH}px`,
      } as CSSProperties}
      aria-label={p.ariaLabel}
      aria-haspopup={p.onPick ? "dialog" : undefined}
      onMouseEnter={p.onHover ? (event) => p.onHover?.(p.cardId, event.currentTarget, p.x, p.y, p.w, p.h) : undefined}
      onFocus={p.onFocus ? () => p.onFocus?.(p.cardId) : undefined}
      onClick={p.onPick ? (event) => p.onPick?.(p.cardId, event.currentTarget) : undefined}
      onAnimationEnd={settleTile}
    >
      {p.showCard ? (
        <span className="tile-card" aria-hidden="true" style={TILE_CARD_STYLE}>
          {/* frame 入面嘅 tile 全部喺 viewport 內，一律 eager；優先級先分高低。
              late tile 嘅圖 load 好先淡入（tile-img-gated），首輪嗰批唔套。 */}
          <CardImg
            src={p.imageSrc}
            srcSet={p.imageSrcSet}
            sizes={p.sizes}
            loading="eager"
            fetchPriority={p.fetchPriority}
            alt={p.alt}
            className={p.late ? "tile-img-gated" : undefined}
          />
        </span>
      ) : null}
      {p.move ? (
        <span className="tile-move" style={{ fontSize: p.fontSize }} aria-hidden="true">{p.move}</span>
      ) : null}
    </button>
  );
});

/* 圖優先級：頭 12 名或者卡闊 ≥ 60px 先 high，其餘 low */
export function tileFetchPriority(rank: number, cardW: number): "high" | "low" {
  return rank <= 12 || cardW >= 60 ? "high" : "low";
}

/* sizes 落 24px 一格：拖動中 tile 每幀微縮，唔好每幀都改 srcset 選圖 */
export function tileImageSizes(cardW: number): string {
  return `${Math.max(48, Math.ceil(cardW / 24) * 24)}px`;
}
