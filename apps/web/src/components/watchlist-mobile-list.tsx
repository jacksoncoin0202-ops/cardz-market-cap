"use client";

import Link from "next/link";
import { GripVertical } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { CardImage } from "./card-image";
import { Sparkline } from "./sparkline";
import { copy } from "@/lib/i18n";
import { formatMetricMoney, formatPercent, metricTone } from "@/lib/format";
import type { Currency, Locale, MarketCardView, MarketViewSnapshot } from "@/lib/types";

/* moumen drag-to-reorder 概念：watchlist 手機清單撳實手柄拖住排，
   順序記入 localStorage，重新整理後保留 */
const ORDER_KEY = "cardzmc-watchlist-order-v1";

function applySavedOrder(cards: MarketCardView[]): MarketCardView[] {
  try {
    const raw = window.localStorage.getItem(ORDER_KEY);
    if (!raw) return cards;
    const saved: unknown = JSON.parse(raw);
    if (!Array.isArray(saved)) return cards;
    const rank = new Map(cards.map((card, index) => [card.id, index]));
    const weight = new Map<string, number>();
    saved.forEach((id, index) => {
      if (typeof id === "string" && rank.has(id)) weight.set(id, index);
    });
    if (!weight.size) return cards;
    return [...cards].sort((a, b) => (weight.get(a.id) ?? rank.get(a.id)! + saved.length) - (weight.get(b.id) ?? rank.get(b.id)! + saved.length));
  } catch {
    return cards;
  }
}

function persistOrder(cards: MarketCardView[]) {
  try {
    window.localStorage.setItem(ORDER_KEY, JSON.stringify(cards.map((card) => card.id)));
  } catch { /* 私隱模式記唔到就算，唔擋拖放 */ }
}

export function WatchlistMobileList({ cards, locale, currency, snapshot, href }: {
  cards: MarketCardView[];
  locale: Locale;
  currency: Currency;
  snapshot: MarketViewSnapshot;
  href: (path: string) => string;
}) {
  const t = copy[locale];
  const [ordered, setOrdered] = useState<MarketCardView[]>(cards);
  const [drag, setDrag] = useState<{ id: string; dy: number } | null>(null);
  const rowsRef = useRef(new Map<string, HTMLElement>());
  const liveRef = useRef<{ id: string; pointerId: number; startY: number; lastDy: number } | null>(null);

  /* 後端卡單每日會變：saved order 做主軸，新卡按原本排名插入 */
  useEffect(() => {
    setOrdered(applySavedOrder(cards));
  }, [cards]);

  const onPointerDown = useCallback((event: React.PointerEvent<HTMLButtonElement>, card: MarketCardView) => {
    event.preventDefault();
    liveRef.current = { id: card.id, pointerId: event.pointerId, startY: event.clientY, lastDy: 0 };
    setDrag({ id: card.id, dy: 0 });
    event.currentTarget.setPointerCapture(event.pointerId);
  }, []);

  const onPointerMove = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    const live = liveRef.current;
    if (!live || event.pointerId !== live.pointerId) return;
    const dy = event.clientY - live.startY;
    live.lastDy = dy;
    setDrag({ id: live.id, dy });

    const draggedRow = rowsRef.current.get(live.id);
    if (!draggedRow) return;
    const draggedRect = draggedRow.getBoundingClientRect();
    const draggedMiddle = draggedRect.top + draggedRect.height / 2;

    setOrdered((current) => {
      const from = current.findIndex((card) => card.id === live.id);
      if (from === -1) return current;
      let to = from;
      if (dy > 0 && from < current.length - 1) {
        const below = rowsRef.current.get(current[from + 1].id);
        if (below) {
          const rect = below.getBoundingClientRect();
          if (draggedMiddle > rect.top + rect.height / 2) to = from + 1;
        }
      } else if (dy < 0 && from > 0) {
        const above = rowsRef.current.get(current[from - 1].id);
        if (above) {
          const rect = above.getBoundingClientRect();
          if (draggedMiddle < rect.top + rect.height / 2) to = from - 1;
        }
      }
      if (to === from) return current;
      const next = [...current];
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return next;
    });
  }, []);

  const endDrag = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    const live = liveRef.current;
    if (!live || event.pointerId !== live.pointerId) return;
    liveRef.current = null;
    setDrag(null);
    setOrdered((current) => {
      persistOrder(current);
      return current;
    });
  }, []);

  return (
    <div className="mobile-ranking-list watchlist-sortable" data-dragging={drag ? "true" : undefined}>
      <div className="mobile-list-header" aria-hidden="true">
        <span className="mobile-col-info">{t.labels.card}</span>
        <span className="mobile-col-right">{t.labels.priceShort}</span>
        <span className="mobile-col-spark">{t.labels.salesTrendShort}</span>
      </div>
      {ordered.map((card) => {
        const change = card.windows["7d"].changePct;
        const tone = metricTone(change);
        const isDragging = drag?.id === card.id;
        return (
          <div
            className={`mobile-rank-card watchlist-row${isDragging ? " is-dragging" : ""}`}
            key={card.id}
            ref={(node) => {
              if (node) rowsRef.current.set(card.id, node);
              else rowsRef.current.delete(card.id);
            }}
            style={isDragging ? { transform: `translateY(${drag.dy}px)` } : undefined}
          >
            <button
              type="button"
              className="drag-grip"
              aria-label={`${card.name[locale] || t.status.unavailable} — reorder`}
              onPointerDown={(event) => onPointerDown(event, card)}
              onPointerMove={onPointerMove}
              onPointerUp={endDrag}
              onPointerCancel={endDrag}
            >
              <GripVertical aria-hidden="true" size={15} strokeWidth={1.8} />
            </button>
            <Link className="watchlist-row-link" href={href(`/card/${card.id}`)}>
              <span className="mobile-rank-index">{card.rank}</span>
              <div className="ranking-thumb"><CardImage image={card.image} sizes="56px" /></div>
              <div className="mobile-card-info">
                <span className="mobile-card-number">{card.collectorNumber}</span>
                <strong className="mobile-card-name">{card.name[locale] || t.status.unavailable}</strong>
                <span className="mobile-card-sub">
                  <span className="mobile-card-cap">{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</span>
                </span>
              </div>
              <div className="mobile-card-right">
                <span className="mobile-card-price">{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</span>
                <span className={`mobile-change-badge metric-${tone}`}>{formatPercent(change, locale)}</span>
              </div>
              <Sparkline points={card.historyDaily} label={t.labels.salesTrend} />
            </Link>
          </div>
        );
      })}
    </div>
  );
}
