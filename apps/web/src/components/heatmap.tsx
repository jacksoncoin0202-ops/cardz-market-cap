"use client";

import { AnimatePresence, motion } from "framer-motion";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type FocusEvent as ReactFocusEvent, type KeyboardEvent as ReactKeyboardEvent, type PointerEvent as ReactPointerEvent, type RefObject } from "react";
import { createPortal, flushSync } from "react-dom";
import { CapTicker } from "./cap-ticker";
import { CardImage, srcSet as cardSrcSet } from "./card-image";
import { HeatmapKioskFx } from "./heatmap-kiosk-fx";
import { HeatmapTile, tileFetchPriority, tileImageSizes } from "./heatmap-tile";
import { PeriodSelector } from "./period-selector";
import { DETAIL_PRINT_FIELDS, printIdentityRows } from "./print-badge";
import { ShareMenu } from "./share-menu";
import { TuneColor, TuneRange, useParamPump, useTuneCommit } from "./tune-panel";
import { Sheet } from "./ui/sheet";
import { displayCardName } from "@/lib/card-name";
import { copy } from "@/lib/i18n";
import { formatDate, formatMetricInteger, formatMetricMoney, formatMoney, formatObservationDate, formatPercent, formatTrackedSales, metricTone } from "@/lib/format";
import { tap } from "@/lib/haptic";
import { snapCardBox, snapFrameGrid, snapTileBox } from "@/lib/pixel-snap";
import { heatmapTreemapLayout } from "@/lib/ranked-strip-layout";
import { heatmapOgFilename, heatmapOgLang, heatmapOgPath, type HeatmapOgScope, type HeatmapOgTheme } from "@/lib/heatmap-og";
import { shareImageBlob } from "@/lib/share-file";
import { SHARE_TARGETS, type ShareFormat, type ShareTarget } from "@/lib/share-destinations";
import { fetchShareBlob } from "@/lib/share-fetch";
import {
  DEFAULT_SHARE_RESOLUTION,
  SHARE_RESOLUTIONS,
  type ShareResolution,
} from "@/lib/share-resolution";
import { changeValue, DEFAULT_TILE, tileCardSize, tileColors, tileStyle, type TileParams } from "@/lib/tile-style";
import { useMarketSettings } from "@/lib/use-market-settings";
import { useUpDown } from "@/lib/use-updown";
import type { Currency, Locale, MarketCardView, MarketViewSnapshot, MarketWindow } from "@/lib/types";
import "@/app/styles/heatmap-tune.css";
import "@/app/styles/heatmap-kiosk.css";

interface HeatmapProps {
  cards: MarketCardView[];
  locale: Locale;
  currency: Currency;
  snapshot: MarketViewSnapshot;
  href: (path: string) => string;
  title: string;
  scope: HeatmapOgScope;
}

/*
 * 邊個 tile 貼住 frame 邊（treemap 出嚟嘅 x/y 係 frame 座標，0 同 frameW/frameH 就係邊）。
 * 用途：frame 係 `overflow:hidden` + `--section-radius` 圓角，而 tile 一律 4px 方角，
 * 所以四個角嗰格會俾 frame 切走一橛 —— hover 嗰條 2px 白線行到角位就斷（owner
 * 2026-08-18：「白色嗰條線食咗個角頭」）。標咗 data-corner 之後 CSS 會俾嗰格
 * 跟返 frame 個弧，線就收得返入去。半徑喺 CSS 度砌，呢度只講「邊格喺邊個角」。
 * 0.5px 容差：treemap 出嘅係浮點，唔可以用 === 0 比。
 */
const EDGE_EPS = 0.5;
function cornerToken(tile: { x: number; y: number; width: number; height: number } | undefined, w: number, h: number): string {
  if (!tile || !w || !h) return "";
  const left = tile.x <= EDGE_EPS;
  const top = tile.y <= EDGE_EPS;
  const right = tile.x + tile.width >= w - EDGE_EPS;
  const bottom = tile.y + tile.height >= h - EDGE_EPS;
  /* 一格可以食兩個角（例如成條左邊都係佢），所以係 token list 唔係單一值 */
  return [top && left ? "tl" : "", top && right ? "tr" : "", bottom && left ? "bl" : "", bottom && right ? "br" : ""]
    .filter(Boolean).join(" ");
}

/* ✗ 呢度本來有個 `fxRadius()`（tile 離板中心嘅歸一距離），淨係餵 kiosk 入場 burst 個
   `animation-delay`。burst 2026-08-21 剷咗（scale tile 盒食走 pixel-snap 白隙 → 走位，
   實測見 app/styles/heatmap-kiosk.css §6），佢跟住零 call site，所以一齊剷。 */

function CardFacts({ card, locale, currency, snapshot, period }: Omit<HeatmapProps, "cards" | "href" | "title" | "scope"> & { card: MarketCardView; period: MarketWindow }) {
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
function CardDialog({ card, locale, currency, snapshot, href, onClose, period, returnFocusRef }: Omit<HeatmapProps, "cards" | "title" | "scope"> & {
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

/*
 * ── Kiosk 全屏（owner 2026-08-18：店主想一撳就自動鋪滿成塊屏，橫直都要）────────────
 *
 * 幾何唔使我哋做：treemap 收 frame 實際闊高、frame 有 ResizeObserver，換咗容器佢自己重排。
 * 呢度只負責「入 / 出」同「全屏期間嘅店舖行為」（wake lock + 定時 refresh）。
 * 樣全部喺 app/styles/heatmap-kiosk.css。
 */

/* 兩條路都要落嘅 <html> class，作用係收走 page scrollbar **同埋**佢預留嗰條 gutter
   （`overflow: hidden` + `scrollbar-gutter: auto`，見 styles/heatmap-kiosk.css 嗰段註）。
   **唔係為咗靚，係為咗真係佔滿。** 全屏元素係 fixed，fixed 嘅 containing block 唔計 gutter，
   而個站成日鎖住 `scrollbar-gutter: stable` —— 唔拆就實測 frame 1265 vs innerWidth 1280，
   右邊 15px 唔係熱力圖。Windows / Linux Chrome 係實心 scrollbar，店主部電視就係呢個情況。 */
const KIOSK_HTML_CLASS = "heatmap-kiosk";
/* 假全屏（冇 Fullscreen API 嗰條路）先加呢個。**唔可以落喺 section 上面**：
   要收埋 site header / footer / 榜單，佢哋全部係 section 嘅祖先或者兄弟。 */
const KIOSK_FALLBACK_CLASS = "heatmap-kiosk-fallback";
/* 退出全屏之後釘住 scroll 幾多幀（點解要釘：見退出 effect 嗰段長註）。
   實測 anchoring 喺 t+19ms 同 t+62ms 推兩次，10 幀 ≈ 160ms 蓋得住，同時短到用戶察覺唔到。 */
const KIOSK_SCROLL_HOLD_FRAMES = 10;

/* 品牌 logo（owner 明文「要 show 翻個公司 logo」）。兩張都係 vector，擺幾大都唔會糊。
   **只准 kiosk 開咗之後先 render**：light 版 59 KB，平時就派落嚟即係首頁首屏白白多 59 KB
   （DESIGN.md 開章：任何「加多啲視覺」第一個問題係會唔會令第一屏慢咗）。
   width/height 抄返檔案真實 viewBox（light 969.29×419.45、dark 946.82×384.98）——
   **兩張比例唔同**，共用一組數會扁咗其中一張。theme 揀邊個檔跟返 header.tsx 個 logoByTheme。 */
const KIOSK_LOGO = {
  light: { src: "/brand/logo-cardz-marketcap.svg", width: 969, height: 419 },
  dark: { src: "/brand/logo-cardz-marketcap-dark.svg", width: 947, height: 385 },
} as const;

/* 店舖長開：snapshot 每日 bake，唔定時 refresh 就會掛住琴日個數字直到有人掂部機。
   5 分鐘係 router.refresh()（RSC payload，唔係成版 reload），tile 唔會閃走。 */
const KIOSK_REFRESH_MS = 5 * 60 * 1000;


/*
 * Kiosk 特效 kill switch。五個 token 默認全開，落喺 `[data-kiosk-fx~="…"]`，
 * CSS 每個特效自己 gate 返自己嗰個 token。
 * 出口係 URL query（`?kioskfx=breathe,tour`）唔係 localStorage：部機掛喺牆上面冇 devtools，
 * 店主改個書籤就熄得，我哋 debug 都唔使入 console。`?kioskfx=` 空值 = 五個全熄（總掣）。
 * 只認呢五個字：attribute 係我哋自己 render 落 DOM，唔准俾 URL 塞任意字串入去。
 * 冇 `enter`：入場動畫 2026-08-21 剷晒（heatmap-kiosk.css §6），冇嘢可以熄。
 */
const KIOSK_FX_TOKENS = ["breathe", "sweep", "tour", "edge", "noise"] as const;
function readKioskFx(): string {
  if (typeof window === "undefined") return KIOSK_FX_TOKENS.join(" "); // useState initializer 喺 SSR 都會行
  const raw = new URLSearchParams(window.location.search).get("kioskfx");
  if (raw === null) return KIOSK_FX_TOKENS.join(" ");
  const want = new Set(raw.split(/[\s,]+/));
  return KIOSK_FX_TOKENS.filter((tok) => want.has(tok)).join(" ");
}

/* 四角向外 = 入全屏；四角向內 = 退出。同隔離 .heatmap-tune-toggle 一套畫法
   （16×16 viewBox 24、stroke currentColor、strokeWidth 2、round cap）。 */
const KIOSK_ICON_ENTER = "M4 9V4h5M15 4h5v5M20 15v5h-5M9 20H4v-5";
const KIOSK_ICON_EXIT = "M9 4v5H4M20 9h-5V4M15 20v-5h5M4 15h5v5";

export function Heatmap({ cards, locale, currency, snapshot, href, title, scope }: HeatmapProps) {
  const { period, theme } = useMarketSettings();
  /* 升跌色慣例（F13）：red-up 就將 up/down 兩組色對調——tune 參數意義不變（「升色」永遠係用戶心目中嘅升色）。 */
  const { resolved: upDown, setPref: setUpDownPref } = useUpDown(locale);
  const t = copy[locale];
  const frameRef = useRef<HTMLDivElement>(null);
  /* frame 喺絕對 device px 格上嘅 layout 盒（闊高 + dpr + origin 偏移）—— tile 幾何全部靠佢釘格 */
  const [size, setSize] = useState({ width: 0, height: 0, dpr: 1, originX: 0, originY: 0 });
  const [sheetCard, setSheetCard] = useState<MarketCardView | null>(null);
  const [pickedCount, setPickedCount] = useState<number | null>(null);
  const [showTune, setShowTune] = useState(false);
  const tuneToggleRef = useRef<HTMLButtonElement>(null);
  const isMobileTiles = useSyncExternalStore(subscribeMobileTiles, () => window.matchMedia(mobileTilesQuery).matches, () => false);
  const dark = theme === "dark";
  const [hoverStore] = useState(createHoverStore);
  const cardsById = useMemo(() => new Map(cards.map((card) => [card.id, card])), [cards]);

  /* ── Kiosk 全屏 ───────────────────────────────────────────────────────────────
     target 係 **section 唔係 frame**：咁標題、總市值、legend 先會跟住入全屏
     （native 全屏只 render 全屏元素個 subtree，揀咗 frame 就淨返一版無名色格）。 */
  const router = useRouter();
  const sectionRef = useRef<HTMLElement>(null);
  const kioskToggleRef = useRef<HTMLButtonElement>(null);
  const [kiosk, setKiosk] = useState(false);
  /* 讀一次就釘死：URL 中途唔會變，而中途變 token list 會令 CSS animation 重播（成版閃）。
     SSR/hydration 安全：`data-kiosk-fx` 只喺 kiosk===true 先 render，而 kiosk 兩邊都由 false 起。 */
  const [kioskFx] = useState(readKioskFx);
  /* 而家行緊邊條路。用 ref 唔用 state：fullscreenchange handler 要即刻讀到最新值
     （假全屏期間 native fullscreenElement 一定係 null，唔分開就會即刻自己關咗自己）。 */
  const cssKioskRef = useRef(false);
  const kioskScrollRef = useRef(0);
  /* tile handler 讀呢個（唔係讀 state）：handler 一律要釘死身份，HeatmapTile 個 memo 先有效。 */
  const kioskRef = useRef(false);
  useEffect(() => { kioskRef.current = kiosk; }, [kiosk]);

  const enterKiosk = useCallback(() => {
    const section = sectionRef.current;
    if (!section) return;
    kioskScrollRef.current = window.scrollY; // 一定要喺加 class 之前讀（html overflow:hidden 會 clamp 到 0）
    const fallback = () => {
      cssKioskRef.current = true;
      document.documentElement.classList.add(KIOSK_FALLBACK_CLASS);
      setKiosk(true);
    };
    document.documentElement.classList.add(KIOSK_HTML_CLASS);
    /* iPhone Safari **冇** Element.requestFullscreen（iPad 有）。呢個唔係「舊瀏覽器」而係
       一大批真店主部機，所以攞唔到 API 就落 CSS 假全屏，兩條路都要行得通。 */
    if (typeof section.requestFullscreen === "function") {
      /* 成功嗰下唔喺度 setKiosk：交返俾 fullscreenchange 收尾，同 ESC／瀏覽器自己退出行同一條路，
         state 先冇機會卡喺「以為仲喺全屏」。 */
      section.requestFullscreen().catch(fallback); // 用戶拒絕 / iframe 冇 allow / 手勢過期：唔好死
      return;
    }
    fallback();
  }, []);

  const exitKiosk = useCallback(() => {
    if (cssKioskRef.current) {
      cssKioskRef.current = false;
      document.documentElement.classList.remove(KIOSK_HTML_CLASS, KIOSK_FALLBACK_CLASS);
      setKiosk(false);
      return;
    }
    /* 同樣交返俾 fullscreenchange；exitFullscreen 本身拒絕（罕有）先自己落閘，唔好卡住。 */
    if (document.fullscreenElement) document.exitFullscreen().catch(() => setKiosk(false));
    else setKiosk(false);
  }, []);

  const toggleKiosk = useCallback(() => {
    tap.select();
    if (kiosk) exitKiosk();
    else enterKiosk();
  }, [kiosk, enterKiosk, exitKiosk]);

  /* 用戶撳 ESC、撳瀏覽器個「退出全屏」、或者切走 tab 令全屏自動散 —— 全部只會 fire 呢個 event。
     唔聽就會卡喺「以為仲喺全屏」：controls 收埋、frame 撐到盡，但實際上已經返咗普通頁。 */
  useEffect(() => {
    const onChange = () => {
      if (cssKioskRef.current) return; // 假全屏果條路唔關 native 事
      setKiosk(document.fullscreenElement === sectionRef.current);
    };
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  /* 假全屏冇瀏覽器幫手，ESC 要自己補；native 全屏個 ESC 由瀏覽器食咗，keydown 都收唔到。 */
  useEffect(() => {
    if (!kiosk) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && cssKioskRef.current) { event.preventDefault(); exitKiosk(); }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [kiosk, exitKiosk]);

  /* 退出之後拆返 <html> class、還原 scroll 位同 focus。ESC／瀏覽器自己退出／切走 tab 全部
     都會行到呢度，因為佢哋一律經 fullscreenchange → setKiosk(false)。
     次序有意思：一定要**先**拆 overflow:hidden 先 scrollTo，否則捲返去嘅目標位仲係俾 clamp 住。
     behavior 一定要寫 "instant"：html 有 scroll-behavior: smooth，"auto" 嘅意思係「跟 CSS」
     ＝ 播成秒嘅捲動動畫，退出嗰下成版飄兼 focus 落錯位。
     focus 個 preventScroll 唔可以慳（覆核 2026-08-18 實測）：focus() 預設會 scroll-into-view，
     而 html 嗰個 scroll-behavior: smooth 令佢變成一段動畫，**倒轉頭剷走**上一行啱啱還原好嘅
     scroll 位（實測 260 → 0，時間線係 t=142ms 到位 260，跟住 247→72→26→0）。
     只有「退出嗰刻 focus 一直坐喺個掣度」先睇唔出，因為嗰陣 focus() 係 no-op —— 即係話
     喺 kiosk 撳過期間掣、或者用 ESC／瀏覽器 UI 退出，就一定中招。 */
  const kioskWasOnRef = useRef(false);
  useEffect(() => {
    if (kiosk) { kioskWasOnRef.current = true; return; }
    if (!kioskWasOnRef.current) return;
    kioskWasOnRef.current = false;
    document.documentElement.classList.remove(KIOSK_HTML_CLASS, KIOSK_FALLBACK_CLASS);
    const want = kioskScrollRef.current;
    window.scrollTo({ top: want, left: 0, behavior: "instant" });
    kioskToggleRef.current?.focus({ preventScroll: true });
    /* ── 還原完仲要釘住幾幀，否則 scroll anchoring 會自己再推走 ──
       行到上面 scrollTo 嗰刻，section 仲係 `position: fixed`（未返返入 flow），文件矮咗成個
       section 咁多——實測 scrollHeight 10370 → 11022，差 652 = section 高度。reflow 一到，
       瀏覽器 scroll anchoring 為咗維持視覺穩定自己補 scroll ⇒ 260 變 777（差 517，3/3 重現）。
       **唔係 FE05 整出嚟**：換返 HEAD 版 heatmap-kiosk.css 一樣跳、拆走 FX component 一樣跳，
       native 全屏同假全屏兩條路都跳。只喺 `prefers-reduced-motion: reduce` 之下見到。
       行過嘅死路，唔好再行一次：
         · `scroll-behavior` 唔係因——兩邊強行掉轉，個跳位唔跟住走（qa-reduce-scroll.mjs 交叉驗）。
         · 淨係補一針 scrollTo（double rAF）——假全屏修到，native 修唔到：anchoring 喺 t+19ms
           同 t+62ms 推咗**兩次**，一針追唔切。
         · `overflow-anchor: none` 落 <html> inline ——冇用。佢唔繼承，只係話「呢個 element
           自己唔做 anchor」，Chromium 照樣揀個深啲嘅 node 做 anchor。要 `*` 先冚到，代價太大。
       所以釘住到 reflow 完為止：每幀見到郁咗就拉返，最多 KIOSK_SCROLL_HOLD_FRAMES 幀。
       已經喺位就唔寫，用戶喺呢 ~0.16 秒內自己捲，最多俾我哋拉返一兩幀。 */
    let frames = 0;
    let raf = 0;
    const hold = () => {
      if (Math.abs(window.scrollY - want) > 1) window.scrollTo({ top: want, left: 0, behavior: "instant" });
      frames += 1;
      if (frames < KIOSK_SCROLL_HOLD_FRAMES) raf = requestAnimationFrame(hold);
    };
    raf = requestAnimationFrame(hold);
    return () => cancelAnimationFrame(raf);
  }, [kiosk]);

  /* 走咗去第二版（soft nav）而仲喺 kiosk：component 一 unmount 就冇人再拆 class，
     成個站會卡喺 overflow:hidden + 榜單 invisible。呢句係最後一道保險。 */
  useEffect(() => () => {
    document.documentElement.classList.remove(KIOSK_HTML_CLASS, KIOSK_FALLBACK_CLASS);
  }, []);

  /* Wake lock：店舖開足全日，唔攞就熄屏。**一 blur 就會俾系統自動釋放**（切 tab、鎖屏），
     所以返到前景一定要重攞，否則等於冇做過。冇 API（iOS Safari < 16.4 等）就靜靜跳過。 */
  useEffect(() => {
    if (!kiosk || typeof navigator === "undefined" || !("wakeLock" in navigator)) return;
    let dropped = false;
    let sentinel: WakeLockSentinel | null = null;
    const acquire = () => {
      if (dropped || sentinel || document.visibilityState !== "visible") return;
      navigator.wakeLock.request("screen").then((next) => {
        if (dropped) { next.release().catch(() => undefined); return; }
        sentinel = next;
        next.addEventListener("release", () => { if (sentinel === next) sentinel = null; });
      }).catch(() => undefined); // 電量低 / 政策唔俾：唔係錯，照顯示落去
    };
    acquire();
    const onVisible = () => { if (document.visibilityState === "visible") acquire(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      dropped = true;
      document.removeEventListener("visibilitychange", onVisible);
      sentinel?.release().catch(() => undefined);
      sentinel = null;
    };
  }, [kiosk]);

  /* 定時 refresh：只喺 kiosk 期間行，退出即清 timer（非 kiosk 期間偷偷每 5 分鐘打 server
     就係無端端嘅背景流量）。router.refresh() 只換 RSC payload，tile 唔會整版閃走。 */
  useEffect(() => {
    if (!kiosk) return;
    const id = window.setInterval(() => router.refresh(), KIOSK_REFRESH_MS);
    return () => window.clearInterval(id);
  }, [kiosk, router]);

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

  /*
   * 分享圖比例唔再係一個 state。owner 2026-08-20 之前呢度有個 `shareAspect`
   * localStorage 記住「4:5 定闊版」，但由 08-19 加咗分享選單之後每個 call site
   * 都已經明講揀邊個 slot，個 state 一路淨係喺 tune panel 度自己同自己講嘢。
   * 而家比例由**目的地**決定（lib/share-destinations.ts），冇得亦唔應該記住 ——
   * 今次 post 去 IG，下次 post 去 WhatsApp Status，記住上次係幫倒忙。
   */
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
  /* tune panel 嘅「升色／跌色」picker 要綁住*實際畫升／畫跌*嘅參數（red-up 對調），同上面 colors 一致 */
  const upParamKey = upDown === "red-up" ? (dark ? "downDark" : "downLight") : (dark ? "upDark" : "upLight");
  const downParamKey = upDown === "red-up" ? (dark ? "upDark" : "upLight") : (dark ? "downDark" : "downLight");

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
    /* 釘落絕對 device px 格 + 冇變就唔 set：sub-pixel 抖動唔好觸發成版 100 格重排。
       要傳 frame 嘅 viewport 位置（rect.left/top）：Chrome snap 嘅係絕對座標，唔傳就右／下邊多一粒（見 pixel-snap.ts）。
       dpr 喺呢度一齊讀：browser zoom 會改 frame 闊度 → ResizeObserver 一定 fire → dpr 跟住更新。 */
    const measure = (width: number, height: number, left: number, top: number) => {
      const next = snapFrameGrid(width, height, window.devicePixelRatio || 1, left, top);
      setSize((prev) => (
        prev.width === next.width && prev.height === next.height && prev.dpr === next.dpr
          && prev.originX === next.originX && prev.originY === next.originY ? prev : next
      ));
    };
    const observer = new ResizeObserver(() => {
      const next = frame.getBoundingClientRect();
      frameRectRef.current = next;
      measure(next.width, next.height, next.left, next.top);
    });
    observer.observe(frame);
    const rect = frame.getBoundingClientRect();
    frameRectRef.current = rect;
    if (rect.width > 0 && rect.height > 0) measure(rect.width, rect.height, rect.left, rect.top);
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
      /* 角落格跟返 frame 圓角（見檔頭 cornerToken）。同 role/tabindex 一樣行 DOM 直寫，
         唔加 props —— HeatmapTile 係 memo，多一個 props 就多一批 re-render。 */
      const corner = cornerToken(tiles[i], size.width, size.height);
      if ((el.getAttribute("data-corner") ?? "") !== corner) {
        if (corner) el.setAttribute("data-corner", corner);
        else el.removeAttribute("data-corner");
      }
      /* Kiosk 特效嘅 custom property（styles/heatmap-kiosk.css 讀）：
         `--fx-i` = tile 序（呼吸波錯開相位）。得返一個 —— `--fx-r` 同入場 burst 一齊剷咗。
         同 data-corner 一樣行 DOM 直寫，唔加 props —— HeatmapTile 係 memo。
         只喺 kiosk 開咗先寫：平時冇任何 rule 讀佢，慳返每次 commit 100 次 setProperty
         （呢個 effect 冇 dep array，逐 commit 行；hover / slider / ticker 都會經過）。
         退出 kiosk 特登**唔** remove：留住冇人讀，而 remove 要多行 100 次 DOM write。 */
      if (kiosk) {
        const fi = String(i);
        if (el.style.getPropertyValue("--fx-i") !== fi) el.style.setProperty("--fx-i", fi);
      }
    });
    tileElsRef.current = map;
  });
  /* tile 實際 box（扣 gap + 釘落 device px 格，見 lib/pixel-snap.ts）——render 同 hover preview 用同一組數；
     index 對 index 就係 tiles[i] */
  const tileBoxes = useMemo(
    () => tiles.map(({ x, y, width, height }) => snapTileBox(x, y, width, height, params.gap, size)),
    [tiles, params.gap, size],
  );
  const tileGeom = useMemo(() => new Map(tiles.map(({ item }, i) => [item.card.id, tileBoxes[i]])), [tiles, tileBoxes]);
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
       preview 又係 display:none，唔好白 render portal。
       Kiosk 同理但原因唔同：preview 係 portal 落 document.body，native 全屏只 render section
       個 subtree（假全屏果條路個 z 又低過 section），兩條路都見唔到 —— 唔好白做。 */
    if (isMobileTilesRef.current || kioskRef.current) return;
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
    /* Kiosk 之下唔開 sheet：<Sheet> 一樣係 portal 落 document.body，喺 native 全屏根本
       render 唔到出嚟 —— 開咗即係「撳咗一下乜都冇」，仲衰過唔俾撳。讀 ref 唔讀 state：
       呢個 handler 要釘死身份，HeatmapTile 個 memo 先有效（heat-perf 合約）。 */
    if (kioskRef.current) return;
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

  /*
   * 分享圖：同卡片內頁一樣 GET server OG。period / 格數 / 榜 / 主題 / 升跌色 / 語言
   * 全部寫入 query，auto-update 用同一條 URL。hover 就 warm，撳掣只 await 已飛緊嘅
   * promise —— OG 有 40 格卡圖，撳完先 fetch 會過 iOS activation。
   */
  const shareBlobs = useRef(new Map<string, Promise<Blob>>());
  const imageLang = heatmapOgLang(locale);
  const ogTheme: HeatmapOgTheme = dark ? "dark" : "light";
  /*
   * 清晰度（owner 2026-08-21）。預設 1080p —— 4K 未 cache 要成分鐘，唔准做預設。
   * 呢個 state 唔入 URL：佢係「今次落載想要幾大」，唔係頁面狀態，寫入 URL 就會連
   * 分享出去嗰條 link 都拖住人哋等 4K。
   */
  const [shareRes, setShareRes] = useState<ShareResolution>(DEFAULT_SHARE_RESOLUTION);
  const shareKey = useCallback(
    (format: ShareFormat, res: ShareResolution) =>
      `${format}|${imageLang}|${activePeriod}|${visibleCount}|${scope}|${ogTheme}|${upDown}|${res}`,
    [imageLang, activePeriod, visibleCount, scope, ogTheme, upDown],
  );
  const warmShareImage = useCallback((format: ShareFormat, res: ShareResolution = shareRes) => {
    const key = shareKey(format, res);
    if (shareBlobs.current.has(key)) return;
    const path = heatmapOgPath({
      period: activePeriod,
      show: visibleCount,
      scope,
      format,
      theme: ogTheme,
      updown: upDown,
      lang: imageLang,
      res,
      /*
       * owner 2026-08-21：「右上角嗰個日子，一定要變返我截圖嗰一刻嘅日子，並不是呢個
       * 官方數據嘅日子。時間要跟返用戶當地嘅時間（例如 HKT）。」
       * → 網站個掣一律 `stamp=now` + 部機自己個時區。route 嘅預設仍然係 `data`，
       *   og:image unfurl 同 HERMES 條 cron 鏈唔受影響（見 lib/share-stamp.ts）。
       */
      stamp: "now",
      tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
      /* 釘死「而家」（歸到分鐘，同 server `readStampAt` 一樣），令 retry 每次都
         行返同一條 cache key。冇呢個 pin，4K 過咗一分鐘就變新 key，永遠 miss。 */
      at: Math.floor(Date.now() / 60_000) * 60_000,
    });
    const pending = fetchShareBlob(path, res, "heatmap OG")
      .catch((error) => {
        shareBlobs.current.delete(key);
        throw error;
      });
    shareBlobs.current.set(key, pending);
  }, [shareKey, shareRes, imageLang, activePeriod, visibleCount, scope, ogTheme, upDown]);
  const exportHeatmap = useCallback(async (target: ShareTarget) => {
    warmShareImage(target.format);
    const blob = await shareBlobs.current.get(shareKey(target.format, shareRes))!;
    const filenameBase = heatmapOgFilename({
      period: activePeriod,
      show: visibleCount,
      scope,
      format: target.format,
      theme: ogTheme,
      updown: upDown,
      lang: imageLang,
      res: shareRes,
    });
    const pageUrl = window.location.href;
    const shareTitle = `${title.replace("{count}", String(visibleCount))} · ${t.periods[activePeriod]}`;
    const outcome = await shareImageBlob(blob, {
      filenameBase,
      title: shareTitle,
      text: `${shareTitle}\n${pageUrl}`,
      clipboardFallbackText: pageUrl,
    });
    if (outcome === "dismissed") return outcome;
    if (isMobileTiles) {
      document.getElementById("market-ranking")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
    return outcome;
  }, [warmShareImage, shareKey, shareRes, imageLang, activePeriod, visibleCount, scope, ogTheme, upDown, title, t.periods, isMobileTiles]);

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
      {/* 分享：先揀清晰度＋去邊，再 GET /api/og/heatmap（period/scope/format/theme/
          updown/lang/res/stamp/tz）。onWarm 同卡片內頁一樣：OG 有幾十格卡圖，撳完先
          fetch 會過 iOS activation。 */}
      <ShareMenu
        surface="heatmap"
        triggerClassName="heatmap-export"
        copy={{
          label: t.labels.shareImage,
          pick: t.labels.shareTo,
          status: t.labels.shareToStatus,
          other: t.labels.shareToOther,
          desktop: t.labels.shareToDesktop,
          portrait: t.labels.shareToPortrait,
          widescreen: t.labels.shareToWidescreen,
          frame: t.labels.shareRatioFrame,
          done: t.share.done,
          error: t.share.error,
        }}
        quality={{
          label: t.labels.shareQuality,
          slowNote: t.labels.shareQualitySlow,
          options: SHARE_RESOLUTIONS,
          value: shareRes,
          onChange: (next) => {
            setShareRes(next);
            /* 換完級即刻開始跑（4K 冷 cache 要成分鐘）。一定要用 `next` 唔用 `shareRes`
               —— `setShareRes` 只係排咗次 re-render，呢一句仲讀住舊嗰級。 */
            warmShareImage(SHARE_TARGETS[0].format, next);
          },
        }}
        onPick={exportHeatmap}
        onWarm={(target) => warmShareImage(target.format)}
        /* 開一次選單 = 一個時刻。唔倒 cache 就會攞返上次開嗰陣張圖，右上角個戳
           印住幾個鐘之前嘅鐘（見 share-menu.tsx `onOpen`）。 */
        onOpen={() => shareBlobs.current.clear()}
      />
      {/* Kiosk 全屏（owner 2026-08-18 店主展示模式）。aria-pressed 講狀態、aria-label 跟住換字，
          唔可以淨靠 icon —— 讀屏睇唔到「四角向內定向外」。 */}
      <button
        ref={kioskToggleRef}
        type="button"
        className="heatmap-kiosk-toggle"
        onClick={toggleKiosk}
        aria-label={kiosk ? t.heatmap.exitFullscreen : t.heatmap.fullscreen}
        aria-pressed={kiosk}
        title={kiosk ? t.heatmap.exitFullscreen : t.heatmap.fullscreen}
      >
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d={kiosk ? KIOSK_ICON_EXIT : KIOSK_ICON_ENTER} />
        </svg>
      </button>
    </div>
  );

  /* data-kiosk = kiosk 總掣；data-kiosk-fx = 逐個特效嘅 token list（見上面 readKioskFx）。
     兩個都淨係 kiosk 期間存在，退出即刻連 CSS 一齊斷乾淨。 */
  return (
    <section className="heatmap-section" ref={sectionRef} data-kiosk={kiosk ? "true" : undefined} data-kiosk-fx={kiosk ? kioskFx : undefined} aria-labelledby="heatmap-heading">
      <div className="heatmap-heading">
        <div className="heatmap-title">
          {/* 呢個係市場頁唯一嘅 H1（owner 2026-08-16 晚：「一入到去就係成個熱力圖」——
              hero 文案搬咗落頁尾做 h2，首屏只留呢個標題 + 總市值一行）。
              owner 2026-08-17：「文字遷就返個熱力圖」—— 標題永遠一行（CSS nowrap + 跟容器闊度縮字級），
              heading 唔再擺 heatmap.body 描述句（換語言唔會再偷 heatmap 高度；
              描述句保留喺下面 legend 嘅 aria-label 同 share image）。 */}
          <h1 id="heatmap-heading">{title.replace("{count}", String(cards.length))}</h1>
          {/* 總市值用 CapTicker：載入 / 期間切換 / 拉 slider 都係由上一個顯示值滾去新值，唔會跳字 */}
          <p className="heatmap-total-cap">{t.labels.marketCap} · <CapTicker value={totalCap} format={formatTotalCap} /></p>
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
        {tiles.map(({ item }, i) => {
          const card = item.card;
          const box = tileBoxes[i];
          const st = tileStyle(changeValue(card, activePeriod), box.w, box.h, colors, params);
          /* 卡圖 box 都釘格：闊高整數 device px、置中餘量雙數，卡邊唔會半粒 pixel 糊 */
          const cardBox = snapCardBox(box.w, box.h, st.cardW, st.cardH, size.dpr);
          const entry = entries.get(card.id) ?? LATE_ENTRY;
          /* 讀屏一句聽晒：名、編號、期間變幅、市值 */
          const label = `#${card.viewRank} ${displayCardName(card, locale, t.status.unavailable)}, ${card.collectorNumber}, ${t.periods[activePeriod]} ${t.labels.change} ${formatPercent(card.windows[activePeriod].changePct, locale)}, ${t.labels.marketCap} ${formatMetricMoney(card.marketCap, currency, snapshot.rates, locale, true)}`;
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
              delay={entry.delay}
              late={entry.late}
              imageSrc={card.image.url}
              imageSrcSet={cardSrcSet(card.image)}
              sizes={tileImageSizes(cardBox.cardW)}
              fetchPriority={tileFetchPriority(card.viewRank, cardBox.cardW)}
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
        {/* Kiosk 特效層（ghost / stage / ring / sweep / noise / edge / 資訊板）+ 自動巡遊。
            一定要喺 .heatmap-frame 入面：ghost/stage/ring 寫嘅係 tile 嘅 offsetParent 座標（= frame），
            而且要俾 frame 個 overflow:hidden 兜住飛到中間嗰張大卡。
            `kiosk &&` 唔准拆：非 kiosk 期間 7 個 node + 一條 5.26s timer 鏈全部唔應該存在，
            而 unmount 就係我哋唯一嘅 cleanup 觸發點（退出全屏 = kiosk 轉 false = unmount）。 */}
        {kiosk && (
          <HeatmapKioskFx
            sectionRef={sectionRef}
            frameRef={frameRef}
            tileElsRef={tileElsRef}
            tiles={tiles}
            tileBoxes={tileBoxes}
            width={size.width}
            height={size.height}
            locale={locale}
            currency={currency}
            rates={snapshot.rates}
            period={activePeriod}
            unavailable={t.status.unavailable}
            moveUp={colors.up}
            moveDown={colors.down}
          />
        )}
      </div>
      {/* footer 淨返一行 legend（owner 2026-08-19）：
          ⚠️ 刪咗「{visibleCount} / {cards.length} 張合資格卡牌」同「查看前 N」——
          兩者都係重複資訊：格數個 slider 自己有數字讀出（`.tile-slider-value`），
          排行榜喺同一頁 scroll 落去就見到，唔使一條 44px 高嘅 jump link 佔住。
          `.heatmap-section` 係 `grid-template-rows: auto minmax(0,1fr) auto`，
          footer 縮矮 = 熱力圖直接高返嗰個數（桌面 ~46px、手機 ~40px），
          owner 明文：文字遷就熱力圖。methodology 全文一路都喺 site footer + share image。 */}
      <div className="heatmap-footer">
        {/* 本來就係一組並列項目，用 ul/li 出返語意，抽取器同讀屏都攞得到。 */}
        <ul className="heatmap-legend" aria-label={t.heatmap.body}>
          <li><span className="legend-swatch down" />{t.heatmap.negative}</li>
          <li><span className="legend-swatch pending" />{t.heatmap.neutral}</li>
          <li><span className="legend-swatch up" />{t.heatmap.positive}</li>
        </ul>
      </div>
      {/* 公司 logo（owner 明文要求）擺右下角，同左下角 legend 對角、離左上標題最遠。
          `kiosk &&` 唔准拆：呢兩個 SVG 加埋 71 KB，平時 render 就係首屏白白多一個請求。 */}
      {kiosk && (
        <img
          className="heatmap-kiosk-logo"
          src={KIOSK_LOGO[theme].src}
          width={KIOSK_LOGO[theme].width}
          height={KIOSK_LOGO[theme].height}
          alt="CardZ Marketcap"
        />
      )}
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
          {/*
            * 升跌慣例（owner 2026-08-16 晚）：header 個 ↕ 反轉咗色，呢度嘅「升色／跌色」都要跟住反轉，
            * 而且喺 panel 入面都俾人揀。red-up 時 tile 用 params.down* 畫升、params.up* 畫跌
            * （見上面 colors useMemo），所以「升色」picker 要綁住 down*，改落去先真係改到升色。
            * key 帶 upDown：uncontrolled color input 換綁定要 remount 先出正確 defaultValue。
            */}
          {/* 呢度以前有個「分享圖比例」分段掣（4:5 / 闊版）。owner 2026-08-20 起比例由
              分享選單嘅**目的地**決定，唔再係一個要預先入 panel 揀嘅設定 —— 撳分享嗰刻
              先揀，仲快同仲準。panel 而家淨返色同格局。 */}
          <div className="tune-field" role="group" aria-label={upDown === "red-up" ? t.labels.upDownRed : t.labels.upDownGreen}>
            <div className="tune-panel-actions tune-updown-actions">
              <button type="button" aria-pressed={upDown === "green-up"} data-active={upDown === "green-up" ? "true" : "false"} onClick={() => setUpDownPref("green-up")}>{t.labels.upDownGreen}</button>
              <button type="button" aria-pressed={upDown === "red-up"} data-active={upDown === "red-up" ? "true" : "false"} onClick={() => setUpDownPref("red-up")}>{t.labels.upDownRed}</button>
            </div>
          </div>
          <div key={`${tuneResetKey}-${dark ? "d" : "l"}-${upDown}`}>
            <TuneColor label={`${t.heatmap.upColor}${dark ? "（Dark）" : "（Light）"}`} value={params[upParamKey]} onInput={(v) => setParam(upParamKey, v)} />
            <TuneColor label={`${t.heatmap.downColor}${dark ? "（Dark）" : "（Light）"}`} value={params[downParamKey]} onInput={(v) => setParam(downParamKey, v)} />
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
