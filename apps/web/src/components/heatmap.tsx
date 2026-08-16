"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type FocusEvent as ReactFocusEvent, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import { createPortal, flushSync } from "react-dom";
import { CapTicker } from "./cap-ticker";
import { CardImage, srcSet as cardSrcSet } from "./card-image";
import { CopyButton } from "./copy-button";
import { HeatmapTile, tileFetchPriority, tileImageSizes } from "./heatmap-tile";
import { PeriodSelector } from "./period-selector";
import { DETAIL_PRINT_FIELDS, printIdentityRows } from "./print-badge";
import { TuneColor, TuneRange, useParamPump, useTuneCommit } from "./tune-panel";
import { Sheet } from "./ui/sheet";
import { displayCardName } from "@/lib/card-name";
import { copy } from "@/lib/i18n";
import { formatDate, formatMetricInteger, formatMetricMoney, formatMoney, formatObservationDate, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { tap } from "@/lib/haptic";
import { heatmapTreemapLayout } from "@/lib/ranked-strip-layout";
import { drawQr } from "@/lib/qr";
import { changeValue, DEFAULT_TILE, tileCardSize, tileColors, tileStyle, type TileParams } from "@/lib/tile-style";
import { PUBLIC_CANONICAL_HOST, PUBLIC_SITE_URL } from "@/lib/public-site";
import { useMarketSettings } from "@/lib/use-market-settings";
import { useUpDown } from "@/lib/use-updown";
import type { Currency, Locale, MarketCardView, MarketViewSnapshot, MarketWindow } from "@/lib/types";

interface HeatmapProps {
  cards: MarketCardView[];
  locale: Locale;
  currency: Currency;
  snapshot: MarketViewSnapshot;
  href: (path: string) => string;
  title: string;
}

function CardFacts({ card, locale, currency, snapshot, period }: Omit<HeatmapProps, "cards" | "href" | "title"> & { card: MarketCardView; period: MarketWindow }) {
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
      <div><dt>{t.periods[period]} {t.labels.change}</dt><dd className={`metric-${metricTone(windowMetric.changePct)}`} title={windowMetric.changePct.sourceSwitched ? t.provenance.anchorSwitched : undefined}>{formatPercent(windowMetric.changePct, locale)}</dd></div>
    </dl>
  );
}

/* 手機 bottom sheet / desktop dialog 共用 <Sheet>（scroll lock、focus trap、Esc、拉落收、退場動畫全部喺入面）。
   card 變 null 嗰次 render 係 open=false，AnimatePresence 會用上一次嘅 children 播退場，所以內容唔會半路消失。 */
function CardDialog({ card, locale, currency, snapshot, href, onClose, period, returnFocusRef }: Omit<HeatmapProps, "cards" | "title"> & {
  card: MarketCardView | null;
  onClose: () => void;
  period: MarketWindow;
  returnFocusRef: RefObject<HTMLElement | null>;
}) {
  const t = copy[locale];
  const closeRef = useRef<HTMLButtonElement>(null);
  return (
    <Sheet
      open={card !== null}
      onClose={onClose}
      variant="sheet"
      backdropClassName="sheet-backdrop"
      panelClassName="bottom-sheet card-dialog"
      ariaLabelledBy="sheet-title"
      initialFocusRef={closeRef}
      returnFocusRef={returnFocusRef}
    >
      {card && (
        <>
          {/* handle + close 一齊做拉落區；內容碌唔到嗰陣成塊 sheet 都拉得。
              touch-action:none 要 inline：冇佢瀏覽器一開始碌就 pointercancel，framer 個 drag 即刻死（實測 iPhone 13 拉唔郁）。 */}
          <div className="sheet-drag-region" style={DRAG_REGION_STYLE}>
            <div className="sheet-handle" />
            <button ref={closeRef} className="sheet-close" type="button" onClick={onClose}>{t.labels.close}</button>
          </div>
          <div className="sheet-card-layout">
            <div className="sheet-image"><CardImage image={card.image} sizes="(max-width: 680px) 80vw, 340px" alt={displayCardName(card, locale)} /></div>
            <div>
              <p className="rank-kicker">#{card.viewRank} / {card.tcg}</p>
              <h3 id="sheet-title">{displayCardName(card, locale, t.status.unavailable)}</h3>
              <p className="muted-copy">{card.setName[locale] || t.status.unavailable}</p>
              <CardFacts card={card} locale={locale} currency={currency} snapshot={snapshot} period={period} />
              <Link className="primary-action" href={href(`/card/${card.id}`)}>{t.labels.viewCard}</Link>
            </div>
          </div>
        </>
      )}
    </Sheet>
  );
}

const DRAG_REGION_STYLE = { touchAction: "none" } as const;

const MOBILE_TILE_COUNT = 23;
const mobileTilesQuery = "(max-width: 680px)";

function subscribeMobileTiles(onChange: () => void) {
  const media = window.matchMedia(mobileTilesQuery);
  media.addEventListener("change", onChange);
  return () => media.removeEventListener("change", onChange);
}

/* Hover preview 用一個極細 external store，唔入 Heatmap 嘅 state：
   掃過一格只會令 <HoverPreview> 一個 component 重 render，100 格 tile 一格都唔郁。 */
interface HoverState {
  cardId: string;
  /* preview 對角擺位：right = 去右邊、bottom = 去下邊（tile 喺左→右，喺上→下） */
  right: boolean;
  bottom: boolean;
  /* 指緊嗰格嘅 viewport rect（fixed 定位用） */
  left: number;
  top: number;
  w: number;
  h: number;
}
function createHoverStore() {
  let state: HoverState | null = null;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    set(next: HoverState | null) {
      if (next === state) return;
      state = next;
      listeners.forEach((fn) => fn());
    },
    subscribe(fn: () => void) {
      listeners.add(fn);
      return () => { listeners.delete(fn); };
    },
  };
}
type HoverStore = ReturnType<typeof createHoverStore>;
const noHover = () => null;

function HoverPreview({ store, cardsById, locale, currency, snapshot, period }: {
  store: HoverStore;
  cardsById: Map<string, MarketCardView>;
  locale: Locale;
  currency: Currency;
  snapshot: MarketViewSnapshot;
  period: MarketWindow;
}) {
  const t = copy[locale];
  const hover = useSyncExternalStore(store.subscribe, store.get, noHover);
  const mounted = useSyncExternalStore(subscribeNoop, readTrue, readFalse);
  const ref = useRef<HTMLElement>(null);
  /* preview 用 fixed 對齊 viewport，脫離 heatmap-frame 嘅 overflow 裁切。
     位置喺 layout effect 用真實 offsetWidth/offsetHeight 夾（唔再假設 520×380），
     paint 之前已經寫好，唔會閃一下先跳位。 */
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || !hover) return;
    const M = 12;
    const pw = el.offsetWidth, ph = el.offsetHeight;
    const vw = window.innerWidth, vh = window.innerHeight;
    let left = hover.right ? hover.left + hover.w + M : hover.left - pw - M;
    let top = hover.bottom ? hover.top + hover.h + M : hover.top - ph - M;
    left = Math.max(M, Math.min(left, vw - pw - M));
    top = Math.max(M, Math.min(top, vh - ph - M));
    el.style.left = `${left}px`;
    el.style.top = `${top}px`;
  }, [hover]);
  if (!mounted) return null;
  const active = hover ? cardsById.get(hover.cardId) : undefined;
  /* 退場（120ms fade/scale）期間 hover 已經係 null：AnimatePresence 會用上一次 render 嘅 props 繼續出內容 */
  return createPortal(
    <AnimatePresence>
      {hover && active ? (
        <motion.aside
          key="preview"
          ref={ref}
          className={`heatmap-preview heatmap-preview-fixed${hover.right ? "" : " preview-left"}${hover.bottom ? "" : " preview-top"}`}
          /* inline animation:none 壓住 globals.css 嘅 preview-in，退場先由 framer 話事 */
          style={PREVIEW_NO_CSS_ANIMATION}
          initial={{ opacity: 0, y: 6, scale: 0.99 }}
          animate={{ opacity: 1, y: 0, scale: 1, transition: { duration: 0.17, ease: [0.22, 1, 0.36, 1] } }}
          exit={{ opacity: 0, scale: 0.98, transition: { duration: 0.12, ease: [0.4, 0, 1, 1] } }}
        >
          <div className="preview-image"><CardImage image={active.image} sizes="220px" alt={displayCardName(active, locale)} /></div>
          <div className="preview-copy">
            <p className="rank-kicker">#{active.viewRank} / {active.tcg}</p>
            <h3>{displayCardName(active, locale, t.status.unavailable)}</h3>
            <p className="muted-copy">{active.setName[locale] || t.status.unavailable}</p>
            <CardFacts card={active} locale={locale} currency={currency} snapshot={snapshot} period={period} />
            <p className="preview-time">{t.labels.asOf}: {formatObservationDate(active.windows[period].changePct.asOf ?? active.pricePsa10.asOf, locale)}</p>
          </div>
        </motion.aside>
      ) : null}
    </AnimatePresence>,
    document.body,
  );
}
const PREVIEW_NO_CSS_ANIMATION = { animation: "none" } as const;
const subscribeNoop = () => () => {};
const readTrue = () => true;
const readFalse = () => false;

interface TileEntry { late: boolean; delay: number; }
const LATE_ENTRY: TileEntry = { late: true, delay: 0 };
const ENTRY_WAVE_MS = 240;

export function Heatmap({ cards, locale, currency, snapshot, href, title }: HeatmapProps) {
  const { period, theme } = useMarketSettings();
  /* 升跌色慣例（F13）：red-up 就將 up/down 兩組色對調——tune 參數意義不變（「升色」永遠係用戶心目中嘅升色）。 */
  const { resolved: upDown } = useUpDown(locale);
  const t = copy[locale];
  const frameRef = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [sheetCard, setSheetCard] = useState<MarketCardView | null>(null);
  const [pickedCount, setPickedCount] = useState<number | null>(null);
  const [showTune, setShowTune] = useState(false);
  const tuneToggleRef = useRef<HTMLButtonElement>(null);
  const isMobileTiles = useSyncExternalStore(subscribeMobileTiles, () => window.matchMedia(mobileTilesQuery).matches, () => false);
  const dark = theme === "dark";
  const [hoverStore] = useState(createHoverStore);
  const cardsById = useMemo(() => new Map(cards.map((card) => [card.id, card])), [cards]);

  /* Slider 絲滑三件事（2026-08-16 重寫，取代「拖動格網 + 放手 commit」）：
     1) input 係 uncontrolled（defaultValue）。之前 value={visibleCount} 係 controlled，
        React 每次 render 都會將 DOM value 拉返去舊值 → thumb 郁唔到、放手讀到嘅
        value 亦係舊值（100% 冇反應）兩個 bug 都係呢度出。
     2) fill 條同數字用 DOM 直寫，唔經 React，thumb 100% 跟手。
     3) treemap 同 thumb 同一幀落地：input 事件淨係記低目標值（pendingRef）並排一個
        rAF；同一幀入面幾多個 input 都疊埋一次，rAF 入面 flushSync 同步 render+commit，
        paint 之前 tile 已經跟埋 thumb。永遠 commit 最新 pending，唔會漏最後一格；
        慢機自然跳幀（一幀最多一次 render），唔會排隊塞死。 */
  const sliderRef = useRef<HTMLInputElement>(null);
  const sliderValueRef = useRef<HTMLSpanElement>(null);
  const pendingCountRef = useRef<number | null>(null); // 用戶最新拖到、未 commit 嘅值
  const sliderRafRef = useRef<number | null>(null); // 已排未行嘅 rAF
  const dragBucketRef = useRef<number | null>(null); // 上一次 haptic tick 嘅十位 bucket
  const dragEdgeFiredRef = useRef(false); // 今次 drag 掂 min/max 震過未（一次 drag 一次）
  const paintSliderFill = useCallback((value: number, min: number, max: number) => {
    const input = sliderRef.current;
    if (input) input.style.setProperty("--fill", `${((value - min) / Math.max(1, max - min)) * 100}%`);
    if (sliderValueRef.current) sliderValueRef.current.textContent = String(value);
  }, []);
  const flushSlider = useCallback(() => {
    sliderRafRef.current = null;
    const target = pendingCountRef.current;
    if (target === null) return;
    flushSync(() => setPickedCount(target));
  }, []);
  const beginSliderDrag = useCallback(() => {
    const el = sliderRef.current;
    dragBucketRef.current = el ? Math.floor(Number(el.value) / 10) : null;
    dragEdgeFiredRef.current = false;
  }, []);
  const handleSliderInput = useCallback((event: React.ChangeEvent<HTMLInputElement>) => {
    const el = event.currentTarget;
    const value = Number(el.value);
    const min = Number(el.min), max = Number(el.max);
    paintSliderFill(value, min, max);
    /* 觸覺：掂到 min/max 一次 drag 震一次（雙擊感）；每過一個十位 tick 一下，同一格唔重複 */
    const bucket = Math.floor(value / 10);
    if (value === min || value === max) {
      if (!dragEdgeFiredRef.current) { dragEdgeFiredRef.current = true; tap.edge(); }
    } else if (dragBucketRef.current !== null && bucket !== dragBucketRef.current) {
      tap.tick();
    }
    dragBucketRef.current = bucket;
    pendingCountRef.current = value;
    if (sliderRafRef.current === null) sliderRafRef.current = requestAnimationFrame(flushSlider);
  }, [paintSliderFill, flushSlider]);
  useEffect(() => () => { if (sliderRafRef.current !== null) cancelAnimationFrame(sliderRafRef.current); }, []);

  /* 拖 slider 之前先暖圖：未出場嗰批卡嘅 200w 縮圖一次過拉落 cache，
     tile 浮出嗰下已經有圖，唔會先出空框再等圖 pop。desktop 淨係用戶掂 slider 先做；
     mobile 另有 idle 預拉（見下面 effect），因為手機拉 slider 係主要玩法。 */
  const warmedRef = useRef(false);

  /* B12：手機 heatmap 唔再另開 local period——同 rankings 一樣行 URL 嘅 period（useMarketSettings），
     兩個 selector 永遠同步。period-only 嘅 update() 喺 use-market-settings 入面行 history.replaceState
     （router.replace({scroll:false}) Playwright iPhone 13 實測會由 scrollY 260 跳返 0）。 */
  const activePeriod = period;

  /* localStorage persistence：用戶調色即時 save，refresh 都 keep 住。
     B13：state 由 pump 每幀 flush（applyParams），localStorage 只喺 native change（放手）先寫（persistParams）。 */
  const [params, setParamsState] = useState<TileParams>(() => {
    if (typeof window === "undefined") return DEFAULT_TILE;
    try {
      const raw = localStorage.getItem("cardz-heatmap-params");
      if (raw) return { ...DEFAULT_TILE, ...(JSON.parse(raw) as Partial<TileParams>) };
    } catch { /* 隱私模式 / 壞 JSON 就用預設 */ }
    return DEFAULT_TILE;
  });
  const persistParams = useCallback((next: TileParams) => {
    try { localStorage.setItem("cardz-heatmap-params", JSON.stringify(next)); } catch { /* 寫唔入就算 */ }
  }, []);
  const { set: setParam, peek: peekParams } = useParamPump(params, setParamsState);
  const tuneCommitRef = useTuneCommit(persistParams, peekParams);
  const [tuneResetKey, setTuneResetKey] = useState(0);
  const resetParams = useCallback(() => {
    setParamsState(DEFAULT_TILE);
    persistParams(DEFAULT_TILE);
    setTuneResetKey((k) => k + 1); // uncontrolled input 要 remount 先跟返預設
  }, [persistParams]);
  const closeTune = useCallback(() => setShowTune(false), []);

  const defaultCount = Math.min(isMobileTiles ? MOBILE_TILE_COUNT : cards.length, cards.length);
  const minimumVisibleCount = Math.min(10, cards.length);
  const visibleCount = pickedCount === null
    ? defaultCount
    : Math.min(Math.max(minimumVisibleCount, pickedCount), cards.length);
  const lastTriggerRef = useRef<HTMLElement | null>(null);
  const colors = useMemo(() => {
    const base = tileColors(dark, params);
    return upDown === "red-up" ? { ...base, up: base.down, down: base.up } : base;
  }, [dark, params, upDown]);

  /* 暖圖要同 tile 揀同一張 variant：tile 係 srcset(200w/600w)+sizes 俾瀏覽器揀，
     DPR 3 手機 72px 格會揀 600w；如果淨係預拉 200w，去到真機係 cache miss，
     成個暖圖白做。所以照樣用 srcset+sizes 俾瀏覽器行同一套選圖算法——
     sizes 用「拉到盡（全部 tile）」嗰刻嘅卡闊計，late tile 細，揀出嚟嘅檔最保守。 */
  const warmImages = useCallback(() => {
    if (warmedRef.current || typeof window === "undefined") return;
    if (size.width <= 0 || size.height <= 0) return; // 未量到 frame，下次再試
    warmedRef.current = true;
    const connection = (navigator as Navigator & { connection?: { saveData?: boolean } }).connection;
    if (connection?.saveData) return;
    const fullTiles = heatmapTreemapLayout(
      cards.map((card) => ({ card, rank: card.viewRank, value: Math.max(1, card.marketCap.value ?? 1) })),
      size.width,
      size.height,
    );
    const pending = new Set(cards.slice(visibleCount).map((card) => card.id));
    for (const { item, width, height } of fullTiles) {
      const card = item.card;
      if (!pending.has(card.id)) continue;
      const { cardW } = tileCardSize(width - params.gap, height - params.gap, params);
      const img = new Image();
      img.decoding = "async";
      img.fetchPriority = "low";
      const set = cardSrcSet(card.image);
      if (set) {
        img.sizes = `${Math.max(48, Math.ceil(cardW / 24) * 24)}px`;
        img.srcset = set;
      }
      img.src = card.image.url;
    }
  }, [cards, visibleCount, size.width, size.height, params]);

  /* 手機唔等用戶掂 slider 先暖圖：page 靜落嚟（idle）就預拉未出場嗰 77 張 200w
     縮圖（合共 ~0.9MB），拉到嗰下 tile 一彈就有圖，唔會先出色框再等圖。
     只喺 4g／未知網絡做；2g/3g 或 saveData 就照舊等 pointerdown 先拉。 */
  useEffect(() => {
    if (!isMobileTiles || typeof window === "undefined") return;
    const connection = (navigator as Navigator & { connection?: { saveData?: boolean; effectiveType?: string } }).connection;
    if (connection?.saveData) return;
    if (connection?.effectiveType && connection.effectiveType !== "4g") return;
    const w = window as Window & { requestIdleCallback?: (cb: () => void, opts?: { timeout: number }) => number; cancelIdleCallback?: (id: number) => void };
    if (w.requestIdleCallback) {
      const id = w.requestIdleCallback(warmImages, { timeout: 4000 });
      return () => w.cancelIdleCallback?.(id);
    }
    const id = window.setTimeout(warmImages, 2500);
    return () => window.clearTimeout(id);
  }, [isMobileTiles, warmImages]);

  /* 非拖動嘅外部改動（mobile↔desktop 預設數、hydration）先同步 DOM；
     拖動中（pending 有值）DOM 已經係用戶隻手，唔准掂。
     一定要經 input.value= 寫（唔可以淨睇 DOM 已經啱就跳過）：SSR 當 desktop 出
     value=100，hydrate 之後 mobile 先變 23，React 係改 defaultValue 令 DOM 跟住變，
     但 React 自己個 value tracker 仲記住 "100"——之後用戶第一下直接撳到 100，
     React 見「冇變」就吞咗個 onChange，熱力圖冇反應。經 setter 寫一次先會同步 tracker。 */
  useEffect(() => {
    const pending = pendingCountRef.current;
    if (pending !== null) {
      if (pending !== pickedCount) return; // 仲有一格未 commit（rAF 在飛），DOM 係用戶隻手
      pendingCountRef.current = null; // 最新嗰格已落地，之後外部改動可以再同步 DOM
    }
    const input = sliderRef.current;
    if (input) input.value = String(visibleCount);
    paintSliderFill(visibleCount, minimumVisibleCount, cards.length);
  }, [visibleCount, pickedCount, minimumVisibleCount, cards.length, paintSliderFill]);

  /* frame 嘅 viewport rect 快取：hover preview 用，唔好每次 tile mouseenter 都 gBCR。
     ResizeObserver 同 frame mouseenter 量一次；scroll 收埋 preview 兼標 dirty（null），
     下一次入 tile 先補量——scroll 中唔做 layout read。 */
  const frameRectRef = useRef<DOMRect | null>(null);
  const hoveredTileRef = useRef<HTMLButtonElement | null>(null);
  const clearHover = useCallback(() => {
    hoveredTileRef.current?.removeAttribute("data-hover");
    hoveredTileRef.current = null;
    hoverStore.set(null);
  }, [hoverStore]);
  const measureFrameRect = useCallback(() => {
    const frame = frameRef.current;
    if (frame) frameRectRef.current = frame.getBoundingClientRect();
  }, []);
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    /* 取整 + 冇變就唔 set：sub-pixel 抖動唔好觸發成版 100 格重排 */
    const measure = (width: number, height: number) => {
      const w = Math.round(width), h = Math.round(height);
      setSize((prev) => (prev.width === w && prev.height === h ? prev : { width: w, height: h }));
    };
    const observer = new ResizeObserver(([entry]) => {
      measure(entry.contentRect.width, entry.contentRect.height);
      frameRectRef.current = frame.getBoundingClientRect();
    });
    observer.observe(frame);
    const rect = frame.getBoundingClientRect();
    frameRectRef.current = rect;
    if (rect.width > 0 && rect.height > 0) measure(rect.width, rect.height);
    const onScroll = () => { clearHover(); frameRectRef.current = null; };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      observer.disconnect();
      window.removeEventListener("scroll", onScroll);
    };
  }, [clearHover]);

  const visibleCards = useMemo(() => cards.slice(0, visibleCount), [cards, visibleCount]);
  const totalCap = useMemo(() => visibleCards.reduce((sum, card) => sum + (card.marketCap.value ?? 0), 0), [visibleCards]);
  const formatTotalCap = useCallback((n: number) => formatMoney(n, currency, snapshot.rates, locale, true), [currency, snapshot.rates, locale]);

  const tiles = useMemo(() => heatmapTreemapLayout(
    visibleCards.map((card) => ({ card, rank: card.viewRank, value: Math.max(1, card.marketCap.value ?? 1) })),
    size.width,
    size.height,
  ), [visibleCards, size.height, size.width]);

  /* 浮出動畫每張 tile 派一個固定 delay（--d）同 late 旗，派咗就唔准改——
     改一個仲喺 DOM 嘅 tile 嘅 animation-delay / animation-name 會令佢重播（閃一下）。
     入場波：--d 按 tile 中心離 frame 左上角嘅距離（用 frame 對角線歸一，cap 240ms），
     成版由左上向右下掃出嚟，唔再靠 index×18ms（同一 index 換咗卡就會標錯）。
     拖 slider 加出嚟嘅（data-late）：--d = 0，零錯開（owner 2026-08-16：「一拉就即刻
     飛出嚟」）。「飛出」感覺全靠 pop 由細變大嗰下，一 commit 就即刻開波。
     記憶只喺 layout effect 寫（render 唔准 mutate ref）：mountedEntries = 上一次 commit
     仲喺 DOM 嘅 tile 嘅 entry；render 淨係讀——有記錄照用，冇記錄就按「首輪波
     commit 咗未」派新 entry。離場再入場嘅卡冇記錄 → 當 late pop。 */
  const mountedEntriesRef = useRef(new Map<string, TileEntry>());
  const firstWaveDoneRef = useRef(false);
  const prevEntries = mountedEntriesRef.current;
  const firstWaveDone = firstWaveDoneRef.current;
  const frameDiagonal = Math.hypot(size.width, size.height) || 1;
  const entries = new Map<string, TileEntry>();
  for (const { item, x, y, width, height } of tiles) {
    const id = item.card.id;
    const prev = prevEntries.get(id);
    entries.set(id, prev ?? (firstWaveDone
      ? LATE_ENTRY
      : { late: false, delay: Math.round(ENTRY_WAVE_MS * Math.min(1, Math.hypot(x + width / 2, y + height / 2) / frameDiagonal)) }));
  }
  /* F2 鍵盤：成個 board 一個 tab stop（roving tabindex），方向鍵喺 tile 之間行。
     HeatmapTile 冇 tabIndex / role / data-* props（heat-perf 嘅 memo 合約），所以呢啲 attribute
     喺 layout effect 直接寫落 DOM——React 唔管呢幾個 attribute，re-render 唔會抹走。
     DOM 順序 = tiles 順序（keyed map），所以 index 對 index 就係同一張卡。 */
  const activeTileIdRef = useRef<string | null>(null);
  const tileElsRef = useRef(new Map<string, HTMLButtonElement>());
  useLayoutEffect(() => {
    mountedEntriesRef.current = entries;
    if (tiles.length) firstWaveDoneRef.current = true;
    const frame = frameRef.current;
    if (!frame) return;
    const els = frame.querySelectorAll<HTMLButtonElement>(".heatmap-tile");
    let active = activeTileIdRef.current;
    if (!active || !entries.has(active)) active = tiles[0]?.item.card.id ?? null;
    activeTileIdRef.current = active;
    const map = new Map<string, HTMLButtonElement>();
    els.forEach((el, i) => {
      const id = tiles[i]?.item.card.id;
      if (!id) return;
      map.set(id, el);
      if (el.dataset.cardId !== id) el.dataset.cardId = id;
      if (el.getAttribute("role") !== "option") el.setAttribute("role", "option");
      /* 用 attribute 比對：<button> 冇 attribute 時 .tabIndex 都係 0，會漏寫 */
      const want = id === active ? "0" : "-1";
      if (el.getAttribute("tabindex") !== want) el.setAttribute("tabindex", want);
    });
    tileElsRef.current = map;
  });
  const tileGeom = useMemo(() => {
    const gap = params.gap;
    return new Map(tiles.map(({ item, x, y, width, height }) => [item.card.id, { x: x + gap / 2, y: y + gap / 2, w: width - gap, h: height - gap }]));
  }, [tiles, params.gap]);
  const tileGeomRef = useRef(tileGeom);
  useEffect(() => { tileGeomRef.current = tileGeom; }, [tileGeom]);
  const tilesRef = useRef(tiles);
  useEffect(() => { tilesRef.current = tiles; }, [tiles]);

  const closeSheet = useCallback(() => { setSheetCard(null); }, []);

  /* tile handler 全部釘死（deps 只有 store / map），HeatmapTile 嘅 memo 先有效。
     hover 高亮：直接喺 DOM 落 data-hover（CSS 靠佢升 z-index，唔使 :hover 掃 99 個兄弟），
     preview 內容行 hoverStore，Heatmap 本身唔會因為 hover 重 render。 */
  const isMobileTilesRef = useRef(isMobileTiles);
  useEffect(() => { isMobileTilesRef.current = isMobileTiles; }, [isMobileTiles]);
  const handleTileHover = useCallback((cardId: string, el: HTMLButtonElement, x: number, y: number, w: number, h: number) => {
    /* 手機：touch 嘅 mouseenter 會 stick，唔落 data-hover（免得 tap 完個格「揀死」），
       preview 又係 display:none，唔好白 render portal。 */
    if (isMobileTilesRef.current) return;
    const prev = hoveredTileRef.current;
    if (prev !== el) {
      prev?.removeAttribute("data-hover");
      el.setAttribute("data-hover", "");
      hoveredTileRef.current = el;
    }
    if (!frameRectRef.current) measureFrameRect();
    const fr = frameRectRef.current;
    if (!fr) return;
    // 對角原則：tile 喺左半 → preview 去右邊；右半 → 去左邊；
    // 上半 → 去下邊；下半 → 去上邊。唔會遮住指緊嘅卡。
    hoverStore.set({
      cardId,
      right: x + w / 2 <= fr.width / 2,
      bottom: y + h / 2 <= fr.height / 2,
      left: fr.left + x,
      top: fr.top + y,
      w,
      h,
    });
  }, [hoverStore, measureFrameRect]);
  /* 鍵盤 focus 落 tile = 同 mouseenter 一樣：roving tabindex 換位 + data-hover + preview（frame 級 delegation） */
  const handleFrameFocus = useCallback((event: ReactFocusEvent<HTMLDivElement>) => {
    const el = (event.target as HTMLElement).closest<HTMLButtonElement>(".heatmap-tile");
    const id = el?.dataset.cardId;
    if (!el || !id) return;
    const prevId = activeTileIdRef.current;
    if (prevId !== id) {
      const prevEl = prevId ? tileElsRef.current.get(prevId) : undefined;
      if (prevEl) prevEl.tabIndex = -1;
      el.tabIndex = 0;
      activeTileIdRef.current = id;
    }
    const g = tileGeomRef.current.get(id);
    if (g) handleTileHover(id, el, g.x, g.y, g.w, g.h);
  }, [handleTileHover]);
  const handleFrameBlur = useCallback((event: ReactFocusEvent<HTMLDivElement>) => {
    const next = event.relatedTarget as Node | null;
    if (!next || !event.currentTarget.contains(next)) clearHover();
  }, [clearHover]);
  const handleFrameKeyDown = useCallback((event: ReactKeyboardEvent<HTMLDivElement>) => {
    const el = (event.target as HTMLElement).closest<HTMLButtonElement>(".heatmap-tile");
    const id = el?.dataset.cardId;
    if (!el || !id) return;
    const list = tilesRef.current;
    const index = list.findIndex((tile) => tile.item.card.id === id);
    if (index < 0) return;
    let nextIndex = -1;
    switch (event.key) {
      case "ArrowRight": nextIndex = Math.min(list.length - 1, index + 1); break;
      case "ArrowLeft": nextIndex = Math.max(0, index - 1); break;
      case "Home": nextIndex = 0; break;
      case "End": nextIndex = list.length - 1; break;
      case "ArrowDown":
      case "ArrowUp": {
        /* 空間鄰居：揀垂直方向最近、水平重疊最多嗰格；treemap 冇整齊行列，冇就跌返 rank ±1 */
        const cur = list[index];
        const cx = cur.x + cur.width / 2, cy = cur.y + cur.height / 2;
        const down = event.key === "ArrowDown";
        let best = -1, bestScore = Infinity;
        list.forEach((tile, i) => {
          if (i === index) return;
          const ty = tile.y + tile.height / 2;
          if (down ? ty <= cy : ty >= cy) return;
          const overlap = Math.min(cur.x + cur.width, tile.x + tile.width) - Math.max(cur.x, tile.x);
          const dx = overlap > 0 ? 0 : Math.abs(tile.x + tile.width / 2 - cx);
          const score = Math.abs(ty - cy) + dx * 2;
          if (score < bestScore) { bestScore = score; best = i; }
        });
        nextIndex = best >= 0 ? best : (down ? Math.min(list.length - 1, index + 1) : Math.max(0, index - 1));
        break;
      }
      default: return;
    }
    event.preventDefault();
    const nextId = list[nextIndex]?.item.card.id;
    const nextEl = nextId ? tileElsRef.current.get(nextId) : undefined;
    nextEl?.focus();
  }, []);
  const handleTilePick = useCallback((cardId: string, el: HTMLButtonElement) => {
    const card = cardsById.get(cardId);
    if (!card) return;
    lastTriggerRef.current = el;
    setSheetCard(card);
  }, [cardsById]);

  /* G6 長按（≈450ms、郁超過 10px 就取消）開同一個 sheet：touch/pen 先做，mouse 唔做。
     長按放手嗰下嘅 click 落喺已經開咗嘅 backdrop 上面——backdrop 只認「pointerdown 都喺自己身上」先關，所以唔會即開即關。 */
  const pressRef = useRef<{ id: string; el: HTMLButtonElement; x: number; y: number; timer: number } | null>(null);
  const pressFiredRef = useRef(false);
  const cancelPress = useCallback(() => {
    if (pressRef.current) { window.clearTimeout(pressRef.current.timer); pressRef.current = null; }
  }, []);
  const handleFramePointerDown = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType === "mouse" || !event.isPrimary) return;
    const el = (event.target as HTMLElement).closest<HTMLButtonElement>(".heatmap-tile");
    const id = el?.dataset.cardId;
    if (!el || !id) return;
    cancelPress();
    pressFiredRef.current = false;
    const timer = window.setTimeout(() => {
      pressRef.current = null;
      pressFiredRef.current = true;
      handleTilePick(id, el);
    }, 450);
    pressRef.current = { id, el, x: event.clientX, y: event.clientY, timer };
  }, [cancelPress, handleTilePick]);
  const handleFramePointerMove = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    const press = pressRef.current;
    if (press && Math.hypot(event.clientX - press.x, event.clientY - press.y) > 10) cancelPress();
  }, [cancelPress]);
  const handleFrameContextMenu = useCallback((event: React.MouseEvent<HTMLDivElement>) => {
    /* Android 長按 tile（入面有 img）會喺 ~500ms 彈 context menu，同我哋 450ms 嘅長按撞；
       press 進行中或者啱啱 fire 咗就食咗佢 */
    if (pressRef.current || pressFiredRef.current) event.preventDefault();
  }, []);
  useEffect(() => cancelPress, [cancelPress]);

  /* 富士菲林式分享：heatmap 逐格畫上 canvas，pixel wordmark + 真實數量標題 + QR
     mobile-first：點料用 brand 色，唔好淨係白底黑字 */
  const exportHeatmap = useCallback(async () => {
    if (!size.width || !size.height || !tiles.length) return;
    const scale = Math.min(2, 2400 / size.width);
    const pad = Math.round(28 * scale);
    const headerH = Math.round(120 * scale);
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
    const logo = await loadImage(dark ? "/brand/logo-cardz-marketcap-dark.png" : "/brand/logo-cardz-marketcap.png");
    const stamp = formatDate(new Date().toISOString(), locale);
    const periodLabel = t.periods[activePeriod];
    const shareTitle = `${title.replace("{count}", String(visibleCards.length))} · ${periodLabel} ${t.labels.change}`;
    const shareTitleNarrow = `${title.replace("{count}", String(visibleCards.length))} · ${periodLabel}`;
    const logoH = Math.round(56 * scale);
    const logoW = logo ? Math.round(logoH * (logo.width / logo.height)) : 0;
    const stampFont = Math.round(12 * scale);
    ctx.font = `600 ${Math.round(22 * scale)}px system-ui, sans-serif`;
    const titleW = ctx.measureText(shareTitle).width;
    ctx.font = `500 ${stampFont}px system-ui, sans-serif`;
    const stampW = ctx.measureText(stamp).width;
    const oneLineW = pad + logoW + Math.round(18 * scale) + titleW + Math.round(24 * scale) + stampW + pad;
    const narrow = oneLineW > canvas.width;
    const fitText = (text: string, maxW: number) => {
      if (ctx.measureText(text).width <= maxW) return text;
      let cut = text;
      while (cut.length > 1 && ctx.measureText(`${cut}…`).width > maxW) cut = cut.slice(0, -1);
      return `${cut}…`;
    };
    ctx.textBaseline = "middle";
    if (logo) {
      const logoY = narrow ? pad + Math.round(8 * scale) : headerMidY - logoH / 2;
      ctx.drawImage(logo, pad, logoY, logoW, logoH);
    }
    ctx.font = `500 ${stampFont}px system-ui, sans-serif`;
    ctx.fillStyle = subColor;
    if (narrow) {
      ctx.fillText(stamp, canvas.width - pad - stampW, pad + Math.round(36 * scale));
      ctx.font = `600 ${Math.round(15 * scale)}px system-ui, sans-serif`;
      ctx.fillText(fitText(shareTitleNarrow, canvas.width - pad * 2), pad, pad + Math.round(100 * scale));
    } else {
      ctx.font = `600 ${Math.round(22 * scale)}px system-ui, sans-serif`;
      ctx.fillStyle = textColor;
      ctx.fillText(shareTitle, pad + logoW + Math.round(18 * scale), headerMidY);
      ctx.font = `500 ${stampFont}px system-ui, sans-serif`;
      ctx.fillStyle = subColor;
      ctx.fillText(stamp, canvas.width - pad - stampW, headerMidY);
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
      const st = tileStyle(changeValue(card, activePeriod), tw, th, colors, params);
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
    drawQr(ctx, PUBLIC_SITE_URL, qrCx, qrCy, qrBox, dark ? "#f1f1ee" : "#191917", bgColor);
    ctx.font = `500 ${Math.round(10 * scale)}px system-ui, sans-serif`;
    ctx.fillStyle = subColor;
    const qrLabel = PUBLIC_CANONICAL_HOST;
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
      // 手機：share 完張圖直落排名表。
      if (isMobileTiles) {
        document.getElementById("market-ranking")?.scrollIntoView({ behavior: "smooth", block: "start" });
      }
    }, "image/png");
  }, [size, tiles, visibleCards, title, locale, activePeriod, isMobileTiles, params, colors, dark, t.methodology.body, t.periods, t.labels.change]);

  // Controls 抽返出嚟：desktop 同標題並排，手機由 CSS 將佢哋排喺標題下面、
  // 圖上面（Tiles slider 做主角），結構保持一致。
  const controls = (
    <div className="heatmap-controls">
      <label className="tile-slider">
        <span className="tile-slider-label">{t.heatmap.tilesLabel}</span>
        <input
          type="range"
          min={minimumVisibleCount}
          max={cards.length}
          step={1}
          defaultValue={visibleCount}
          ref={sliderRef}
          onChange={handleSliderInput}
          onPointerDown={() => { beginSliderDrag(); warmImages(); }}
          onFocus={warmImages}
          aria-label={t.heatmap.tilesLabel}
        />
        <span className="tile-slider-value" ref={sliderValueRef} aria-hidden="true">{visibleCount}</span>
      </label>
      <button
        ref={tuneToggleRef}
        type="button"
        className="heatmap-tune-toggle"
        onClick={() => setShowTune(!showTune)}
        aria-label={t.heatmap.customize}
        aria-expanded={showTune}
        aria-haspopup="dialog"
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
  );

  return (
    <section className="heatmap-section" aria-labelledby="heatmap-heading">
      <div className="heatmap-heading">
        <div>
          {/* h2 唔係 h1（GEO，owner 2026-08-16）：H1 已經由 market-page 個 hero 出，
              一版一個 H1。id / aria-labelledby 照舊，CSS `.heatmap-heading h1, h2` 一齊食。 */}
          <h2 id="heatmap-heading">{title.replace("{count}", String(cards.length))}</h2>
          {/* 總市值用 CapTicker：載入 / 期間切換 / 拉 slider 都係由上一個顯示值滾去新值，唔會跳字 */}
          <p className="heatmap-total-cap">{t.labels.marketCap} · <CapTicker value={totalCap} format={formatTotalCap} /></p>
          <p>{t.heatmap.body}</p>
        </div>
        {controls}
      </div>
      {/* F2：board 係一個 listbox（roving tabindex，一個 tab stop），tile 嘅 role/tabindex/data-card-id
          喺上面 layout effect 落 DOM。focus / keydown / 長按全部 frame 級 delegation。 */}
      <div
        className="heatmap-frame"
        ref={frameRef}
        role="listbox"
        aria-label={title.replace("{count}", String(visibleCount))}
        aria-orientation="horizontal"
        onMouseEnter={measureFrameRect}
        onMouseLeave={clearHover}
        onFocus={handleFrameFocus}
        onBlur={handleFrameBlur}
        onKeyDown={handleFrameKeyDown}
        onPointerDown={handleFramePointerDown}
        onPointerMove={handleFramePointerMove}
        onPointerUp={cancelPress}
        onPointerCancel={cancelPress}
        onPointerLeave={cancelPress}
        onContextMenu={handleFrameContextMenu}
      >
        {tiles.map(({ item, x, y, width, height }) => {
          const card = item.card;
          const gap = params.gap;
          const tileW = width - gap;
          const tileH = height - gap;
          const st = tileStyle(changeValue(card, activePeriod), tileW, tileH, colors, params);
          const entry = entries.get(card.id) ?? LATE_ENTRY;
          /* 讀屏一句聽晒：名、編號、期間變幅、市值 */
          const label = `#${card.viewRank} ${displayCardName(card, locale, t.status.unavailable)}, ${card.collectorNumber}, ${t.periods[activePeriod]} ${t.labels.change} ${formatPercent(card.windows[activePeriod].changePct, locale)}, ${t.labels.marketCap} ${formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}`;
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
              delay={entry.delay}
              late={entry.late}
              imageSrc={card.image.url}
              imageSrcSet={cardSrcSet(card.image)}
              sizes={tileImageSizes(st.cardW)}
              fetchPriority={tileFetchPriority(card.viewRank, st.cardW)}
              alt={displayCardName(card, locale)}
              ariaLabel={label}
              onHover={handleTileHover}
              onPick={handleTilePick}
            />
          );
        })}
        {/* 一層 overlay 做 hover dim（CSS：frame:hover 就淡入），代替 99 個兄弟各自 opacity/filter；
            指緊嗰格靠 data-hover 升 z-index 浮喺 overlay 上面 */}
        <div className="heatmap-dim" aria-hidden="true" />
      </div>
      <div className="heatmap-footer">
        {/* 本來就係一組並列項目，用 ul/li 出返語意，抽取器同讀屏都攞得到。 */}
        <ul className="heatmap-legend" aria-label={t.heatmap.body}>
          <li><span className="legend-swatch down" />{t.heatmap.negative}</li>
          <li><span className="legend-swatch pending" />{t.heatmap.neutral}</li>
          <li><span className="legend-swatch up" />{t.heatmap.positive}</li>
          <li className="legend-count">{visibleCount} / {cards.length} {t.heatmap.count}</li>
        </ul>
        <p className="methodology-note">{t.methodology.body}</p>
        <a className="ranking-jump" href="#market-ranking">{t.heatmap.viewRanking.replace("{count}", String(visibleCount))}</a>
      </div>
      <CardDialog card={sheetCard} locale={locale} currency={currency} snapshot={snapshot} href={href} onClose={closeSheet} period={activePeriod} returnFocusRef={lastTriggerRef} />
      <HoverPreview store={hoverStore} cardsById={cardsById} locale={locale} currency={currency} snapshot={snapshot} period={activePeriod} />
      {/* D4：tune panel 都行 <Sheet>（role=dialog、Esc、focus 入去／還返 toggle、scroll lock、退場動畫）。
          B13：range / color 全部 uncontrolled，pump 每幀 flush state，localStorage 喺 native change 先寫。
          key 跟 dark：色 input 嘅 defaultValue 跟住主題換 key，唔會揸住舊值。 */}
      <Sheet
        open={showTune}
        onClose={closeTune}
        variant="panel"
        backdropClassName="tune-panel-backdrop"
        panelClassName="tune-panel"
        ariaLabelledBy="tune-panel-title"
        returnFocusRef={tuneToggleRef}
      >
        <div ref={tuneCommitRef} className="tune-panel-body">
          <div className="tune-panel-head">
            <strong id="tune-panel-title">{t.heatmap.customizeTitle}</strong>
            <div className="tune-panel-actions">
              <button type="button" onClick={resetParams}>{t.heatmap.resetDefault}</button>
              <button type="button" onClick={closeTune}>{t.labels.close}</button>
            </div>
          </div>
          <div key={`${tuneResetKey}-${dark ? "d" : "l"}`}>
            <TuneColor label={`${t.heatmap.upColor}${dark ? "（Dark）" : "（Light）"}`} value={dark ? params.upDark : params.upLight} onInput={(v) => setParam(dark ? "upDark" : "upLight", v)} />
            <TuneColor label={`${t.heatmap.downColor}${dark ? "（Dark）" : "（Light）"}`} value={dark ? params.downDark : params.downLight} onInput={(v) => setParam(dark ? "downDark" : "downLight", v)} />
            <TuneRange label={t.heatmap.intensity} value={params.gamma} min={0.5} max={4} step={0.1} format={(v) => v.toFixed(1)} onInput={(v) => setParam("gamma", v)} />
            <TuneRange label={t.heatmap.neutralZone} value={params.deadzone} min={0} max={5} step={0.5} format={(v) => `±${v}%`} onInput={(v) => setParam("deadzone", v)} />
            <TuneRange label={t.heatmap.gap} value={params.gap} min={0} max={12} step={1} format={(v) => `${v}px`} onInput={(v) => setParam("gap", v)} />
            <TuneRange label={t.heatmap.cardSize} value={params.cardPct} min={0} max={1} step={0.01} format={(v) => `${Math.round(v * 100)}%`} onInput={(v) => setParam("cardPct", v)} />
          </div>
        </div>
      </Sheet>
    </section>
  );
}
