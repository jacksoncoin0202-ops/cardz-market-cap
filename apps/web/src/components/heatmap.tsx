"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { CardImage } from "./card-image";
import { PeriodSelector } from "./period-selector";
import { copy } from "@/lib/i18n";
import { formatDate, formatMetricInteger, formatMetricMoney, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { heatmapTreemapLayout } from "@/lib/ranked-strip-layout";
import { drawQr } from "@/lib/qr";
import { changeValue, DEFAULT_TILE, tileColors, tileStyle, type TileParams } from "@/lib/tile-style";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { Currency, Locale, MarketCardView, MarketViewSnapshot } from "@/lib/types";
import { TunePanel } from "./tune-panel";

interface HeatmapProps {
  cards: MarketCardView[];
  locale: Locale;
  currency: Currency;
  snapshot: MarketViewSnapshot;
  href: (path: string) => string;
  title: string;
  eyebrow: string;
}

function CardFacts({ card, locale, currency, snapshot }: Omit<HeatmapProps, "cards" | "href" | "title" | "eyebrow"> & { card: MarketCardView }) {
  const { period } = useMarketSettings();
  const t = copy[locale];
  const windowMetric = card.windows[period];
  return (
    <dl className="preview-facts">
      <div><dt>{t.labels.number}</dt><dd>{card.collectorNumber}</dd></div>
      <div><dt>{t.labels.marketCap}</dt><dd>{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</dd></div>
      <div><dt>{t.labels.price}</dt><dd>{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</dd></div>
      <div><dt>{t.labels.population}</dt><dd>{formatMetricInteger(card.populationPsa10, locale)}</dd></div>
      <div><dt>{t.periods[period]} {t.labels.trackedSales}</dt><dd>{formatTrackedSales(windowMetric.trackedSales, currency, snapshot.rates, locale)}</dd></div>
      <div><dt>{t.periods[period]} {t.labels.change}</dt><dd className={`metric-${metricTone(windowMetric.changePct)}`}>{formatPercent(windowMetric.changePct, locale)}</dd></div>
    </dl>
  );
}

function CardDialog({ card, locale, currency, snapshot, href, onClose }: Omit<HeatmapProps, "cards" | "title" | "eyebrow"> & { card: MarketCardView; onClose: () => void }) {
  const t = copy[locale];
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    closeRef.current?.focus();
    const keepFocusInside = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
      if (event.key !== "Tab") return;
      const dialog = closeRef.current?.closest<HTMLElement>("[role='dialog']");
      const focusable = dialog ? Array.from(dialog.querySelectorAll<HTMLElement>("button, a[href], [tabindex]:not([tabindex='-1'])")) : [];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable.at(-1)!;
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", keepFocusInside);
    document.body.classList.add("sheet-open");
    return () => {
      document.removeEventListener("keydown", keepFocusInside);
      document.body.classList.remove("sheet-open");
    };
  }, [onClose]);
  return (
    <div className="sheet-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <section className="bottom-sheet card-dialog" role="dialog" aria-modal="true" aria-labelledby="sheet-title">
        <div className="sheet-handle" />
        <button ref={closeRef} className="sheet-close" type="button" onClick={onClose}>{t.labels.close}</button>
        <div className="sheet-card-layout">
          <div className="sheet-image"><CardImage image={card.image} sizes="(max-width: 680px) 80vw, 340px" alt={card.image.alt[locale] || t.labels.imageAlt} /></div>
          <div>
            <p className="rank-kicker">#{card.rank} / {card.tcg}</p>
            <h3 id="sheet-title">{card.name[locale] || t.status.unavailable}</h3>
            <p className="muted-copy">{card.setName[locale] || t.status.unavailable}</p>
            <CardFacts card={card} locale={locale} currency={currency} snapshot={snapshot} />
            <Link className="primary-action" href={href(`/card/${card.id}`)}>{t.labels.viewCard}</Link>
          </div>
        </div>
      </section>
    </div>
  );
}

const MOBILE_TILE_COUNT = 23;
const mobileTilesQuery = "(max-width: 680px)";

function subscribeMobileTiles(onChange: () => void) {
  const media = window.matchMedia(mobileTilesQuery);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

export function Heatmap({ cards, locale, currency, snapshot, href, title, eyebrow }: HeatmapProps) {
  const { period, theme } = useMarketSettings();
  const t = copy[locale];
  const frameRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [active, setActive] = useState<MarketCardView | null>(null);
  const [sheetCard, setSheetCard] = useState<MarketCardView | null>(null);
  const [pickedCount, setPickedCount] = useState<number | null>(null);
  const [params, setParams] = useState<TileParams>(DEFAULT_TILE);
  const isMobileTiles = useSyncExternalStore(subscribeMobileTiles, () => window.matchMedia(mobileTilesQuery).matches, () => false);
  const dark = theme === "dark";
  const tune = useSyncExternalStore(
    (onChange) => { window.addEventListener("popstate", onChange); return () => window.removeEventListener("popstate", onChange); },
    () => new URLSearchParams(window.location.search).has("tune"),
    () => false,
  );
  const defaultCount = Math.min(isMobileTiles ? MOBILE_TILE_COUNT : cards.length, cards.length);
  const visibleCount = pickedCount === null ? defaultCount : Math.min(Math.max(10, pickedCount), cards.length);
  const lastTriggerRef = useRef<HTMLElement | null>(null);
  const colors = tileColors(dark, params);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) => setSize({ width: entry.contentRect.width, height: entry.contentRect.height }));
    observer.observe(frame);
    const rect = frame.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) setSize({ width: rect.width, height: rect.height });
    return () => observer.disconnect();
  }, []);

  const visibleCards = useMemo(() => cards.slice(0, visibleCount), [cards, visibleCount]);

  const tiles = useMemo(() => heatmapTreemapLayout(
    visibleCards.map((card) => ({ card, rank: card.rank, value: Math.max(1, card.marketCap.value ?? 1) })),
    size.width,
    size.height,
  ), [visibleCards, size.height, size.width]);

  const closeSheet = useCallback(() => {
    setSheetCard(null);
    requestAnimationFrame(() => lastTriggerRef.current?.focus());
  }, []);

  /* 富士菲林式分享：heatmap 逐格畫上 canvas，白邊框架 + 標題 + 日期 */
  const exportHeatmap = useCallback(async () => {
    if (!size.width || !size.height || !tiles.length) return;
    const scale = Math.min(2, 2400 / size.width);
    const pad = Math.round(28 * scale);
    const headerH = Math.round(84 * scale);
    const footerH = Math.round(96 * scale);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(size.width * scale) + pad * 2;
    canvas.height = Math.round(size.height * scale) + pad * 2 + headerH + footerH;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.fillStyle = dark ? "#171716" : "#fafaf7";
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = dark ? "#f1f1ee" : "#191917";
    ctx.font = `600 ${Math.round(24 * scale)}px system-ui, sans-serif`;
    ctx.textBaseline = "middle";
    ctx.fillText(title.replace("{count}", String(visibleCards.length)), pad, pad + headerH / 2);
    ctx.font = `500 ${Math.round(13 * scale)}px system-ui, sans-serif`;
    ctx.fillStyle = dark ? "#a0a09b" : "#555550";
    const stamp = formatDate(new Date().toISOString(), locale);
    const brand = `CARDS Market Cap · ${stamp}`;
    ctx.fillText(brand, canvas.width - pad - ctx.measureText(brand).width, pad + headerH / 2);

    const ox = pad;
    const oy = pad + headerH;
    const loadImage = (src: string) => new Promise<HTMLImageElement | null>((resolve) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    });
    const images = await Promise.all(visibleCards.map((card) => loadImage(card.image.url)));

    for (const [index, { x, y, width, height }] of tiles.entries()) {
      const card = visibleCards[index];
      if (!card) continue;
      const gap = params.gap;
      const tx = ox + (x + gap / 2) * scale;
      const ty = oy + (y + gap / 2) * scale;
      const tw = (width - gap) * scale;
      const th = (height - gap) * scale;
      const st = tileStyle(changeValue(card, period), tw, th, colors, params);
      ctx.fillStyle = st.bg;
      ctx.fillRect(tx, ty, tw, th);
      const img = images[index];
      if (img && st.showCard) {
        const cw = st.cardW; const ch = st.cardH;
        const cx = tx + (tw - cw) / 2; const cy = ty + (th - ch) / 2;
        const imgAspect = img.width / img.height;
        const boxAspect = cw / ch;
        let sw = img.width; let sh = img.height; let sx = 0; let sy = 0;
        if (imgAspect > boxAspect) { sw = img.height * boxAspect; sx = (img.width - sw) / 2; }
        else { sh = img.width / boxAspect; sy = (img.height - sh) / 2; }
        ctx.drawImage(img, sx, sy, sw, sh, cx, cy, cw, ch);
      }
      if (st.move) {
        ctx.font = `700 ${st.fontSize * scale}px system-ui, sans-serif`;
        ctx.fillStyle = "rgba(255, 255, 255, 0.92)";
        const mw = ctx.measureText(st.move).width;
        const mx = tx + tw - mw - Math.max(4, tw * 0.05);
        const my = ty + Math.max(10, th * 0.1);
        ctx.fillText(st.move, mx, my);
      }
    }

    /* footer 右邊：QR → 官網；左邊 methodology 兩行，避開 QR 位 */
    const qrBox = Math.round(72 * scale);
    const qrCx = canvas.width - pad - qrBox / 2;
    const qrCy = canvas.height - pad - footerH / 2;
    const methodology = t.methodology.body;
    const qrLeft = canvas.width - pad - qrBox - Math.round(16 * scale);
    ctx.font = `500 ${Math.round(12 * scale)}px system-ui, sans-serif`;
    ctx.fillStyle = dark ? "#a0a09b" : "#555550";
    const maxTextWidth = qrLeft - pad;
    if (ctx.measureText(methodology).width <= maxTextWidth) {
      ctx.fillText(methodology, pad, qrCy);
    } else {
      const mid = Math.floor(methodology.length / 2);
      let split = methodology.indexOf(" ", mid);
      if (split === -1) split = mid;
      ctx.fillText(methodology.slice(0, split), pad, qrCy - Math.round(9 * scale));
      ctx.fillText(methodology.slice(split + 1), pad, qrCy + Math.round(11 * scale));
    }
    drawQr(ctx, "https://cardsmarketcap.com", qrCx, qrCy, qrBox, dark ? "#f1f1ee" : "#191917", dark ? "#171716" : "#fafaf7");
    ctx.font = `500 ${Math.round(10 * scale)}px system-ui, sans-serif`;
    ctx.fillStyle = dark ? "#a0a09b" : "#555550";
    const qrLabel = "cardsmarketcap.com";
    ctx.fillText(qrLabel, qrCx - ctx.measureText(qrLabel).width / 2, qrCy + qrBox / 2 + Math.round(10 * scale));

    canvas.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `cards-heatmap-top${visibleCards.length}-${new Date().toISOString().slice(0, 10)}.png`;
      anchor.click();
      URL.revokeObjectURL(url);
    }, "image/png");
  }, [size, tiles, visibleCards, title, locale, period, params, colors, dark, t.methodology.body]);

  return (
    <section className="heatmap-section" aria-labelledby="heatmap-heading">
      <div className="heatmap-heading">
        <div>
          <p className="section-kicker">{eyebrow}</p>
          <h1 id="heatmap-heading">{title.replace("{count}", String(visibleCards.length))}</h1>
          <p>{t.heatmap.body}</p>
        </div>
        <div className="heatmap-controls">
          <label className="tile-slider">
            <span className="tile-slider-label">{t.heatmap.tilesLabel}</span>
            <input
              type="range"
              min={10}
              max={cards.length}
              step={1}
              value={visibleCount}
              onChange={(event) => setPickedCount(Number(event.target.value))}
              aria-label={t.heatmap.tilesLabel}
            />
            <span className="tile-slider-value" aria-hidden="true">{visibleCount}</span>
          </label>
          <PeriodSelector />
          <button className="heatmap-export" type="button" onClick={exportHeatmap}>
            {t.heatmap.shareImage}
          </button>
        </div>
      </div>
      <div className="heatmap-frame" ref={frameRef} onMouseLeave={() => setActive(null)}>
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
              aria-label={`#${card.rank} ${card.name[locale] || t.status.unavailable}, ${card.collectorNumber}`}
              aria-haspopup="dialog"
              onMouseEnter={() => setActive(card)}
              onFocus={() => setActive(card)}
              onClick={(event) => {
                lastTriggerRef.current = event.currentTarget;
                setSheetCard(card);
              }}
            >
              {st.showCard ? (
                <span
                  className="tile-card"
                  aria-hidden="true"
                  style={{ width: st.cardW, height: st.cardH, left: (tileW - st.cardW) / 2, top: (tileH - st.cardH) / 2 }}
                >
                  <CardImage image={card.image} sizes={`${Math.max(40, Math.round(st.cardW))}px`} loading={card.rank <= 8 ? "eager" : "lazy"} />
                </span>
              ) : null}
              {st.move ? (
                <span className="tile-move" style={{ fontSize: st.fontSize }} aria-hidden="true">{st.move}</span>
              ) : null}
            </button>
          );
        })}
        {active && (
          <aside className="heatmap-preview" aria-live="polite">
            <div className="preview-image"><CardImage image={active.image} sizes="220px" alt={active.image.alt[locale] || t.labels.imageAlt} /></div>
            <div className="preview-copy">
              <p className="rank-kicker">#{active.rank} / {active.tcg}</p>
              <h3>{active.name[locale] || t.status.unavailable}</h3>
              <p className="muted-copy">{active.setName[locale] || t.status.unavailable}</p>
              <CardFacts card={active} locale={locale} currency={currency} snapshot={snapshot} />
              <p className="preview-time">{t.labels.asOf}: {formatDate(active.windows[period].changePct.asOf ?? active.pricePsa10.asOf, locale)}</p>
            </div>
          </aside>
        )}
      </div>
      <div className="heatmap-footer">
        <div className="heatmap-legend" aria-label={t.heatmap.body}>
          <div><span className="legend-swatch down" />{t.heatmap.negative}</div>
          <div><span className="legend-swatch pending" />{t.heatmap.neutral}</div>
          <div><span className="legend-swatch up" />{t.heatmap.positive}</div>
          <div className="legend-count">{visibleCards.length} / {cards.length} {t.heatmap.count}</div>
        </div>
        <p className="methodology-note">{t.methodology.body}</p>
        <a className="ranking-jump" href="#market-ranking">{t.heatmap.viewRanking.replace("{count}", String(visibleCards.length))}</a>
      </div>
      {sheetCard && <CardDialog card={sheetCard} locale={locale} currency={currency} snapshot={snapshot} href={href} onClose={closeSheet} />}
      {tune ? <TunePanel params={params} onChange={setParams} dark={dark} /> : null}
    </section>
  );
}
