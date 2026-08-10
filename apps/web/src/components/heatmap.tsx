"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import { CardImage } from "./card-image";
import { CopyButton } from "./copy-button";
import { PeriodSelector } from "./period-selector";
import { DETAIL_PRINT_FIELDS, printIdentityRows } from "./print-badge";
import { copy } from "@/lib/i18n";
import { formatDate, formatMetricInteger, formatMetricMoney, formatMoney, formatObservationDate, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { heatmapTreemapLayout } from "@/lib/ranked-strip-layout";
import { drawQr } from "@/lib/qr";
import { changeValue, DEFAULT_TILE, tileColors, tileStyle, type TileParams } from "@/lib/tile-style";
import { useMarketSettings } from "@/lib/use-market-settings";
import type { Currency, Locale, MarketCardView, MarketViewSnapshot } from "@/lib/types";

interface HeatmapProps {
  cards: MarketCardView[];
  locale: Locale;
  currency: Currency;
  snapshot: MarketViewSnapshot;
  href: (path: string) => string;
  title: string;
}

function CardFacts({ card, locale, currency, snapshot }: Omit<HeatmapProps, "cards" | "href" | "title"> & { card: MarketCardView }) {
  const { period } = useMarketSettings();
  const t = copy[locale];
  const windowMetric = card.windows[period];
  return (
    <dl className="preview-facts">
      <div><dt>{t.labels.number}</dt><dd>{card.collectorNumber}</dd></div>
      {/* CardFacts 一改，dialog（sheet）同 hover preview 兩個 surface 一次過搞掂。
          冇印刷資料就一條都唔會加，格數同以前一樣。 */}
      {printIdentityRows(card, locale, DETAIL_PRINT_FIELDS).map((row) => (
        <div key={row.key} data-field={row.key}><dt>{row.label}</dt><dd title={row.value}>{row.value}</dd></div>
      ))}
      <div><dt>{t.labels.marketCap}</dt><dd>{formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}</dd></div>
      <div><dt>{t.labels.price}</dt><dd>{formatMetricMoney(card.pricePsa10, currency, snapshot.rates, locale)}</dd></div>
      <div><dt>{t.labels.population}</dt><dd>{formatMetricInteger(card.populationPsa10, locale)}</dd></div>
      <div><dt>{t.periods[period]} {t.labels.trackedSales}</dt><dd>{formatTrackedSales(windowMetric.trackedSales, currency, snapshot.rates, locale)}</dd></div>
      <div><dt>{t.periods[period]} {t.labels.change}</dt><dd className={`metric-${metricTone(windowMetric.changePct)}`} title={windowMetric.changePct.sourceSwitched ? `Historical anchor: ${windowMetric.changePct.priceAnchorSource}` : undefined}>{formatPercent(windowMetric.changePct, locale)}</dd></div>
    </dl>
  );
}

function CardDialog({ card, locale, currency, snapshot, href, onClose }: Omit<HeatmapProps, "cards" | "title"> & { card: MarketCardView; onClose: () => void }) {
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
          <div className="sheet-image"><CardImage image={card.image} sizes="(max-width: 680px) 80vw, 340px" alt={card.officialName ?? ""} /></div>
          <div>
            <p className="rank-kicker">#{card.viewRank} / {card.tcg}</p>
            <h3 id="sheet-title">{card.officialName || t.status.unavailable}</h3>
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

export function Heatmap({ cards, locale, currency, snapshot, href, title }: HeatmapProps) {
  const { period, theme } = useMarketSettings();
  const t = copy[locale];
  const frameRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [active, setActive] = useState<MarketCardView | null>(null);
  // preview 對角擺位：right = 去右邊、bottom = 去下邊（tile 喺左→右，喺上→下）
  const [previewCorner, setPreviewCorner] = useState({ right: true, bottom: true });
  // preview 用 fixed 對齊 viewport，脫離 heatmap-frame 嘅 overflow 裁切
  const [previewPos, setPreviewPos] = useState<{ left: number; top: number } | null>(null);
  const [sheetCard, setSheetCard] = useState<MarketCardView | null>(null);
  const [pickedCount, setPickedCount] = useState<number | null>(null);
  const [showTune, setShowTune] = useState(false);
  const isMobileTiles = useSyncExternalStore(subscribeMobileTiles, () => window.matchMedia(mobileTilesQuery).matches, () => false);
  const dark = theme === "dark";

  /* localStorage persistence：用戶調色即時 save，refresh 都 keep 住 */
  const [params, setParamsState] = useState<TileParams>(() => {
    if (typeof window === "undefined") return DEFAULT_TILE;
    try {
      const raw = localStorage.getItem("cardz-heatmap-params");
      if (raw) return { ...DEFAULT_TILE, ...(JSON.parse(raw) as Partial<TileParams>) };
    } catch { /* 隱私模式 / 壞 JSON 就用預設 */ }
    return DEFAULT_TILE;
  });

  const setParams = useCallback((next: TileParams) => {
    setParamsState(next);
    try { localStorage.setItem("cardz-heatmap-params", JSON.stringify(next)); } catch { /* 寫唔入就算 */ }
  }, [setParamsState]);

  const defaultCount = Math.min(isMobileTiles ? MOBILE_TILE_COUNT : cards.length, cards.length);
  const minimumVisibleCount = Math.min(10, cards.length);
  const visibleCount = pickedCount === null
    ? defaultCount
    : Math.min(Math.max(minimumVisibleCount, pickedCount), cards.length);
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
  const totalCap = useMemo(() => visibleCards.reduce((sum, card) => sum + (card.marketCap.value ?? 0), 0), [visibleCards]);

  const tiles = useMemo(() => heatmapTreemapLayout(
    visibleCards.map((card) => ({ card, rank: card.viewRank, value: Math.max(1, card.marketCap.value ?? 1) })),
    size.width,
    size.height,
  ), [visibleCards, size.height, size.width]);

  const closeSheet = useCallback(() => {
    setSheetCard(null);
    requestAnimationFrame(() => lastTriggerRef.current?.focus());
  }, []);

  /* 富士菲林式分享：heatmap 逐格畫上 canvas，品牌框 + 真實數量標題 + QR
     mobile-first：點料用 brand 色，唔好淨係白底黑字 */
  const exportHeatmap = useCallback(async () => {
    if (!size.width || !size.height || !tiles.length) return;
    const scale = Math.min(2, 2400 / size.width);
    const pad = Math.round(28 * scale);
    const headerH = Math.round(96 * scale);
    const footerH = Math.round(160 * scale);
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(size.width * scale) + pad * 2;
    canvas.height = Math.round(size.height * scale) + pad * 2 + headerH + footerH;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    /* 深色模式用品牌深炭底（同 logo pack 嘅 #0D0D0F 接近），淺色用暖白 */
    const bgColor = dark ? "#0D0D0F" : "#fafaf7";
    const textColor = dark ? "#f1f1ee" : "#191917";
    const subColor = dark ? "#a0a09b" : "#555550";
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    /* header：左邊 logo + 真實數量標題，右邊日期；窄畫布改兩行排，唔准疊字 */
    const headerMidY = pad + headerH / 2;
    const loadImage = (src: string) => new Promise<HTMLImageElement | null>((resolve) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => resolve(null);
      img.src = src;
    });
    const logo = await loadImage(dark ? "/brand/logo-horizontal-dark-512.png" : "/brand/logo-horizontal-light-512.png");
    const icon = await loadImage("/brand/icon-transparent.png");
    const stamp = formatDate(new Date().toISOString(), locale);
    const periodLabel = t.periods[period];
    const shareTitle = `${title.replace("{count}", String(visibleCards.length))} · ${periodLabel} ${t.labels.change}`;
    const shareTitleNarrow = `${title.replace("{count}", String(visibleCards.length))} · ${periodLabel}`;
    const logoH = Math.round(30 * scale);
    const stampFont = Math.round(12 * scale);
    /* 單行 header 實測闊度，擺唔落先用三行 narrow 排版
       （手機 430pt 出 908px 畫布，固定 900 門檻會誤判做闊屏令成個 header 疊埋） */
    ctx.font = `700 ${Math.round(24 * scale)}px system-ui, sans-serif`;
    const brandW = ctx.measureText("CardZMarketcap").width;
    ctx.font = `600 ${Math.round(22 * scale)}px system-ui, sans-serif`;
    const titleW = ctx.measureText(shareTitle).width;
    ctx.font = `500 ${stampFont}px system-ui, sans-serif`;
    const stampW = ctx.measureText(stamp).width;
    const oneLineW = pad + logoH + Math.round(10 * scale) + brandW + Math.round(18 * scale)
      + titleW + Math.round(24 * scale) + stampW + pad;
    const narrow = oneLineW > canvas.width;
    const fitText = (text: string, maxW: number) => {
      if (ctx.measureText(text).width <= maxW) return text;
      let cut = text;
      while (cut.length > 1 && ctx.measureText(`${cut}…`).width > maxW) cut = cut.slice(0, -1);
      return `${cut}…`;
    };
    ctx.textBaseline = "middle";
    if (narrow) {
      /* 窄畫布三行：上行 icon + 日期、中行品牌字、下行標題，全部唔准重疊 */
      const topY = pad + Math.round(22 * scale);
      const iconH = Math.round(26 * scale);
      if (icon) ctx.drawImage(icon, pad, topY - iconH / 2, iconH, iconH);
      ctx.font = `500 ${stampFont}px system-ui, sans-serif`;
      ctx.fillStyle = subColor;
      ctx.fillText(stamp, canvas.width - pad - ctx.measureText(stamp).width, topY);
      ctx.font = `700 ${Math.round(22 * scale)}px system-ui, sans-serif`;
      ctx.fillStyle = textColor;
      ctx.fillText("CardZMarketcap", pad, pad + Math.round(56 * scale));
      ctx.font = `600 ${Math.round(15 * scale)}px system-ui, sans-serif`;
      ctx.fillStyle = subColor;
      ctx.fillText(fitText(shareTitleNarrow, canvas.width - pad * 2), pad, pad + Math.round(82 * scale));
    } else {
      let titleX = pad;
      if (logo) {
        /* 品牌字逐筆重寫（logo 圖係細寫 z，品牌係 CardZ）：圖只畫 icon 部分（前 74/512） */
        const iconCropW = Math.min(logo.width, Math.round(74 * (logo.width / 512)));
        ctx.drawImage(logo, 0, 0, iconCropW, logo.height, pad, headerMidY - logoH / 2, logoH * (iconCropW / logo.height), logoH);
        ctx.font = `700 ${Math.round(24 * scale)}px system-ui, sans-serif`;
        ctx.fillStyle = textColor;
        const brand = "CardZMarketcap";
        ctx.fillText(brand, pad + logoH + Math.round(10 * scale), headerMidY);
        titleX = pad + logoH + Math.round(10 * scale) + ctx.measureText(brand).width + Math.round(18 * scale);
      }
      ctx.fillStyle = textColor;
      ctx.font = `600 ${Math.round(22 * scale)}px system-ui, sans-serif`;
      ctx.fillText(shareTitle, titleX, headerMidY);
      ctx.font = `500 ${stampFont}px system-ui, sans-serif`;
      ctx.fillStyle = subColor;
      ctx.fillText(stamp, canvas.width - pad - ctx.measureText(stamp).width, headerMidY);
    }

    const ox = pad;
    const oy = pad + headerH;
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
        // contain：完整卡圖等比縮放入框，唔准 center-crop 食角
        const fit = Math.min(cw / img.width, ch / img.height);
        const dw = img.width * fit; const dh = img.height * fit;
        ctx.drawImage(img, cx + (cw - dw) / 2, cy + (ch - dh) / 2, dw, dh);
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

    /* footer 右邊：QR → 官網；左邊 methodology，逐字 wrap 避免撳埋 QR 區 */
    const qrBox = Math.round(72 * scale);
    const qrCx = canvas.width - pad - qrBox / 2;
    const qrCy = canvas.height - pad - footerH / 2;
    const methodology = t.methodology.body;
    const qrLeft = canvas.width - pad - qrBox - Math.round(16 * scale);
    ctx.font = `500 ${Math.round(12 * scale)}px system-ui, sans-serif`;
    ctx.fillStyle = subColor;
    const maxTextWidth = qrLeft - pad;
    /* token wrap：英文字/數字成個 token 落行，CJK 逐字，唔准喺 word 中間斷開 */
    const lineHeight = Math.round(20 * scale);
    const tokens = methodology.match(/[\w$][\w,.%$+/-]*|\s+|./g) ?? [methodology];
    const lines: string[] = [];
    let line = "";
    for (const token of tokens) {
      const trial = line + token;
      if (ctx.measureText(trial).width > maxTextWidth && line.trim()) {
        lines.push(line.trimEnd());
        line = token.trimStart();
      } else {
        line = trial;
      }
    }
    if (line.trim()) lines.push(line.trimEnd());
    if (lines.length <= 1) {
      ctx.fillText(methodology, pad, qrCy);
    } else {
      const startY = qrCy - ((lines.length - 1) * lineHeight) / 2;
      lines.forEach((text, i) => ctx.fillText(text, pad, startY + i * lineHeight));
    }
    drawQr(ctx, "https://cardzmarketcap.com", qrCx, qrCy, qrBox, dark ? "#f1f1ee" : "#191917", bgColor);
    ctx.font = `500 ${Math.round(10 * scale)}px system-ui, sans-serif`;
    ctx.fillStyle = subColor;
    const qrLabel = "cardzmarketcap.com";
    ctx.fillText(qrLabel, qrCx - ctx.measureText(qrLabel).width / 2, qrCy + qrBox / 2 + Math.round(10 * scale));

    canvas.toBlob((blob) => {
      if (!blob) return;
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = `cardz-heatmap-top${visibleCards.length}-${new Date().toISOString().slice(0, 10)}.png`;
      anchor.click();
      URL.revokeObjectURL(url);
      navigator.clipboard?.writeText(window.location.href).catch(() => undefined);
    }, "image/png");
  }, [size, tiles, visibleCards, title, locale, period, params, colors, dark, t.methodology.body, t.periods, t.labels.change]);

  return (
    <section className="heatmap-section" aria-labelledby="heatmap-heading">
      <div className="heatmap-heading">
        <div>
          <h1 id="heatmap-heading">{title.replace("{count}", String(visibleCards.length))}</h1>
          <p className="heatmap-total-cap">{t.labels.marketCap} · {formatMoney(totalCap, currency, snapshot.rates, locale, true)}</p>
          <p>{t.heatmap.body}</p>
        </div>
        <div className="heatmap-controls">
          <label className="tile-slider">
            <span className="tile-slider-label">{t.heatmap.tilesLabel}</span>
            <input
              type="range"
              min={minimumVisibleCount}
              max={cards.length}
              step={1}
              value={visibleCount}
              onChange={(event) => setPickedCount(Number(event.target.value))}
              aria-label={t.heatmap.tilesLabel}
            />
            <span className="tile-slider-value" aria-hidden="true">{visibleCount}</span>
          </label>
          <button
            type="button"
            className="heatmap-tune-toggle"
            onClick={() => setShowTune(!showTune)}
            aria-label={t.heatmap.customize}
            aria-expanded={showTune}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="3" />
              <path d="M12 1v6m0 6v6M5.6 5.6l4.2 4.2m4.2 4.2l4.2 4.2M1 12h6m6 0h6M5.6 18.4l4.2-4.2m4.2-4.2l4.2-4.2" />
            </svg>
          </button>
          <PeriodSelector />
          <CopyButton
            className="heatmap-export"
            getText={() => window.location.href}
            label={t.heatmap.shareImage}
            doneLabel={t.labels.shareDone}
            errorLabel={t.labels.shareError}
            onCopy={exportHeatmap}
          />
        </div>
      </div>
      <div className="heatmap-frame" ref={frameRef} onMouseLeave={() => { setActive(null); setPreviewPos(null); }}>
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
              aria-label={`#${card.viewRank} ${card.officialName || t.status.unavailable}, ${card.collectorNumber}`}
              aria-haspopup="dialog"
              onMouseEnter={() => {
                setActive(card);
                // 對角原則：tile 喺左半 → preview 去右邊；右半 → 去左邊；
                // 上半 → 去下邊；下半 → 去上邊。唔會遮住指緊嘅卡。
                const right = tileX + tileW / 2 <= size.width / 2;
                const bottom = tileY + tileH / 2 <= size.height / 2;
                setPreviewCorner({ right, bottom });
                // fixed 定位：用 tile 嘅 viewport rect 計，頂住視窗邊都唔會被 frame 裁
                const frame = frameRef.current;
                if (frame) {
                  const fr = frame.getBoundingClientRect();
                  const PW = 520, PH = 380, M = 12;
                  const vw = window.innerWidth, vh = window.innerHeight;
                  const absL = fr.left + tileX, absT = fr.top + tileY;
                  let left = right ? absL + tileW + M : absL - PW - M;
                  let top = bottom ? absT + tileH + M : absT - PH - M;
                  left = Math.max(M, Math.min(left, vw - PW - M));
                  top = Math.max(M, Math.min(top, vh - PH - M));
                  setPreviewPos({ left, top });
                }
              }}
              onFocus={() => { setActive(card); setPreviewPos(null); }}
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
      {active && previewPos && createPortal(
        <aside
          className={`heatmap-preview heatmap-preview-fixed${previewCorner.right ? "" : " preview-left"}${previewCorner.bottom ? "" : " preview-top"}`}
          style={{ left: previewPos.left, top: previewPos.top }}
          aria-live="polite"
        >
          <div className="preview-image"><CardImage image={active.image} sizes="220px" alt={active.officialName ?? ""} /></div>
          <div className="preview-copy">
            <p className="rank-kicker">#{active.viewRank} / {active.tcg}</p>
            <h3>{active.officialName || t.status.unavailable}</h3>
            <p className="muted-copy">{active.setName[locale] || t.status.unavailable}</p>
            <CardFacts card={active} locale={locale} currency={currency} snapshot={snapshot} />
            <p className="preview-time">{t.labels.asOf}: {formatObservationDate(active.windows[period].changePct.asOf ?? active.pricePsa10.asOf, locale)}</p>
          </div>
        </aside>,
        document.body,
      )}
      {showTune && (
        <div className="tune-panel-backdrop" role="presentation" onClick={() => setShowTune(false)}>
          <aside className="tune-panel" aria-label={t.heatmap.customizeTitle} onClick={(e) => e.stopPropagation()}>
            <div className="tune-panel-head">
              <strong>{t.heatmap.customizeTitle}</strong>
              <div className="tune-panel-actions">
                <button type="button" onClick={() => setParams(DEFAULT_TILE)}>{t.heatmap.resetDefault}</button>
                <button type="button" onClick={() => setShowTune(false)}>{t.labels.close}</button>
              </div>
            </div>
            <label className="tune-field tune-field-color">
              <span>{t.heatmap.upColor}{dark ? "（Dark）" : "（Light）"}</span>
              <input
                type="color"
                value={dark ? params.upDark : params.upLight}
                onChange={(e) => setParams({ ...params, [dark ? "upDark" : "upLight"]: e.target.value })}
              />
              <output>{dark ? params.upDark : params.upLight}</output>
            </label>
            <label className="tune-field tune-field-color">
              <span>{t.heatmap.downColor}{dark ? "（Dark）" : "（Light）"}</span>
              <input
                type="color"
                value={dark ? params.downDark : params.downLight}
                onChange={(e) => setParams({ ...params, [dark ? "downDark" : "downLight"]: e.target.value })}
              />
              <output>{dark ? params.downDark : params.downLight}</output>
            </label>
            <label className="tune-field">
              <span>{t.heatmap.intensity}</span>
              <input
                type="range"
                min={0.5}
                max={4}
                step={0.1}
                value={params.gamma}
                onChange={(e) => setParams({ ...params, gamma: Number(e.target.value) })}
              />
              <output>{params.gamma.toFixed(1)}</output>
            </label>
            <label className="tune-field">
              <span>{t.heatmap.neutralZone}</span>
              <input
                type="range"
                min={0}
                max={5}
                step={0.5}
                value={params.deadzone}
                onChange={(e) => setParams({ ...params, deadzone: Number(e.target.value) })}
              />
              <output>±{params.deadzone}%</output>
            </label>
            <label className="tune-field">
              <span>{t.heatmap.gap}</span>
              <input
                type="range"
                min={0}
                max={12}
                step={1}
                value={params.gap}
                onChange={(e) => setParams({ ...params, gap: Number(e.target.value) })}
              />
              <output>{params.gap}px</output>
            </label>
            <label className="tune-field">
              <span>{t.heatmap.cardSize}</span>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={params.cardPct}
                onChange={(e) => setParams({ ...params, cardPct: Number(e.target.value) })}
              />
              <output>{Math.round(params.cardPct * 100)}%</output>
            </label>
          </aside>
        </div>
      )}
    </section>
  );
}
