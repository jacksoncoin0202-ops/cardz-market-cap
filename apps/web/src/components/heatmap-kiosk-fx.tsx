"use client";

import { useCallback, useEffect, useRef, type RefObject } from "react";
import { tileImageSizes } from "./heatmap-tile";
import { cardNameLangAttr, displayCardName } from "@/lib/card-name";
import { formatMetricMoney, formatPercent, metricTone } from "@/lib/format";
import type { Currency, Locale, MarketCardView, MarketWindow } from "@/lib/types";

/*
 * ═══════════════════════════════════════════════════════════════════════════
 * Kiosk 特效層（FE05）—— overlay + 自動聚光巡遊 + 入場 burst
 * ═══════════════════════════════════════════════════════════════════════════
 *
 * 樣全部喺 `app/styles/heatmap-kiosk.css`（kiosk 已經有自己一個 feature 檔，
 * 而且 `.heatmap-dim` / `.heatmap-kiosk-logo` 兩條係**改緊嗰個檔已經寫咗嘅規則**——
 * 分兩個檔就係「兩個檔各寫一半、下一個人刪錯一邊」嗰個形狀）。
 * 個檔由 `heatmap.tsx:28` import，所以呢度唔使再 import 一次。
 *
 * ── 開關契約（同 CSS 逐隻字對齊，唔准自己另開一套）──────────────────────
 * 1. `.heatmap-section[data-kiosk="true"]`      總掣（heatmap.tsx 已經派）
 * 2. `.heatmap-section[data-kiosk-fx~="…"]`     逐個特效 kill switch（heatmap.tsx 派）
 * 3. `.heatmap-tile` 上面 `--fx-i` / `--fx-r`   heatmap.tsx 個 layout effect 直寫
 * 4. 巡遊狀態**全部係 attribute，冇一個係 React state**（避免每步 re-render 100 格）
 * 5. 飛行幾何寫三個 custom property `--fly-x/--fly-y/--fly-s`，**唔准寫 inline transform**
 *    （inline transform 贏晒所有 stylesheet，reduced-motion sibling 特異度寫到爆都蓋唔到）
 * 6. 時間只有一個真身：`--kiosk-fly-out` / `--kiosk-fly-back` 喺 CSS，
 *    JS 用 `getComputedStyle(frame)` 讀返嚟做時鐘。鏈式 `setTimeout`，唔准 `setInterval`
 *    （撞正 GC／tab 被壓住會排隊補跑，電視上面會連跳兩張）。
 *
 * ⚠️ 呢個 component 只係 `kiosk` state 嘅**消費者**：冇 setKiosk、冇 requestFullscreen、
 *    冇聽 fullscreenchange、冇掂 focus / scroll / slider / hover store。
 */

/* 巡遊池：頭 20 名（tiles[] 本身就係市值排序） */
const TOUR_POOL = 20;
/* hold fallback 個光環離 tile 邊幾多 px */
const RING_PAD = 6;
/* 一輪 = out 900 + HOLD 3200 + 60 + back 700 + GAP 400 = 5260ms。
   HOLD 3200 嘅理由：資訊板要由上到下讀四樣（排名 → 升跌 → 卡名 → 市值），
   舖頭門口行過嘅人喺櫥窗前面大概 4–6 秒，啱啱好睇完整一張。 */
const HOLD_MS = 3200;
const GAP_MS = 400;
/* 板落地之前 120ms 先出資訊板，唔會「啪」一聲彈出嚟 */
const BOARD_LEAD_MS = 120;
/* 飛返之前抖一抖，等 ghost / stage 淡走 */
const BACK_LEAD_MS = 60;
/*
 * 暖機：撳完全屏唔准即刻飛。
 * ① 產品：店主一撳，第一件事應該係見到成塊板落地 + 入場 burst 播完，唔係即刻有卡飛出嚟。
 * ② 工程：C8 個 `verify-kiosk.mjs` 喺入 kiosk 後 1200ms（再等 logo load）量 fillScan /
 *    union bbox，而 gBCR 係計 transform 嘅 —— 飛緊嗰格會令佢見到一個窿。
 *    ⚠️ 呢個唔係「修好咗」：gate 量嘅時間點浮動（1200ms + logo load 0…15s），
 *    而巡遊係 5.26s 一循環嘅無限迴圈，任何固定 delay 都只係搬個機會率。真修法係
 *    gate 側濾走 `[data-kiosk-star]`（coordinator 決定）。
 *    ⚠️⚠️ 2026-08-18 覆核補返一件呢段本身漏咗講嘅事：**入場 burst 自己一樣撞到個 gate**，
 *    而且唔係機會率，係每次都撞。TOUR_WARMUP 擋唔到佢（warmup 係擋飛行，唔係擋 burst）。
 *    實測（1280×720 headless，兩次，`temp/fe05/kiosk-fx-land/adv/adv-c8-burst.mjs`）：
 *      · burst 窗口 = Enter 後 118…1292ms（100 格全部有 transform）
 *      · gate 實際量嘅時間 = 1205…1223ms，嗰刻仲有 15 條 burst animation 未收
 *      · gate 三條數要全部永久達標，最早係 rt ≈ 921…930ms ⇒ 1200ms 淨餘量得 **270…279ms**
 *      · 若果 gate 早過嗰個點量：rt 118…700ms 見到 coverage 0.20…0.36（閘 .93）、
 *        fillScan 0.29…0.46（閘 .999）、unionDH −304.6px（閘 ±8）—— 硬紅
 *      · 仲有第二條窄紅帶 rt ≈ 806…858ms：burst 過衝到 scale 1.035，unionDH = +10.6px > ±8
 *    即係話 BURST_MAX_DELAY + BURST_DUR 任何加長、或者慢機／CI 令 React commit 遲過 270ms，
 *    個 C8 閘就會為咗一個同「格仔有冇填滿」完全無關嘅理由紅。呢條唔准當「已知債」放埋一邊 ——
 *    `temp/fe05/kiosk-fx-land/adv/c8-burst-margin.mjs` 會守住呢 270ms。
 */
const TOUR_WARMUP_MS = 5000;
/* 入場 burst：CSS 冇派 token 就用返呢對（同 CSS `calc(var(--fx-r) * 520ms)` / 620ms 綁死） */
const BURST_MAX_DELAY = 520;
const BURST_DUR = 620;
const BURST_TAIL = 140;

/*
 * 等大圖 decode 嘅上限。**呢個唔係優化，係救命閘**：下一步係喺 `upgradeImage().then()` 入面
 * 先排隊嘅，所以只要 `decode()` 唔 settle，成條巡遊就永遠停喺嗰格，唔會有 error、唔會自己好返。
 * `decode()` 係真係會唔 settle 嘅 —— tab 被壓落背景（Chrome 唔 decode）、請求卡住喺網絡層，
 * 兩樣都唔會 reject。2026-08-18 喺呢部機實測到：preview pane `document.hidden === true`，
 * step() 派咗 `data-kiosk-place` 之後 7.8 秒都冇 `data-fly`，成個巡遊死咗（見交付報告）。
 * 600ms 之後照飛，最多係嗰張卡糊一格。
 */
const DECODE_CAP_MS = 600;

/* ── 純幾何（零 DOM，export 出嚟方便寫 unit test）────────────────────────── */
export const KIOSK_GEOM = {
  /** 闊/高 ≥ 呢個數 → 資訊板貼右（side），否則貼底（below） */
  SIDE_RATIO: 1.15,
  /** 板佔闊度（連 gutter） */
  SIDE_BOARD_FRAC: 0.36,
  /** 板佔高度（連 gutter） */
  BELOW_BOARD_FRAC: 0.32,
  /** 卡最多食 stage 幾多 */
  FIT: 0.82,
  /** 同時唔准超出成個 frame 嘅 70%（owner 要求） */
  FRAME_CAP: 0.7,
  SCALE_CAP: 12,
} as const;

export type KioskPlace = "side" | "below";
export interface KioskStage {
  place: KioskPlace;
  x: number;
  y: number;
  w: number;
  h: number;
  frameW: number;
  frameH: number;
}

/* stage = 卡飛去邊。**stage 已經預先扣走資訊板嗰塊**，所以卡同板結構上唔可能重疊，
   唔係靠肉眼調數。擺位條件只有呢一個真身，CSS 唔准再寫多次 1.15。 */
export function stageBox(frameW: number, frameH: number): KioskStage {
  if (frameH > 0 && frameW / frameH >= KIOSK_GEOM.SIDE_RATIO) {
    return { place: "side", x: 0, y: 0, w: frameW * (1 - KIOSK_GEOM.SIDE_BOARD_FRAC), h: frameH, frameW, frameH };
  }
  return { place: "below", x: 0, y: 0, w: frameW, h: frameH * (1 - KIOSK_GEOM.BELOW_BOARD_FRAC), frameW, frameH };
}

/*
 * FLIP 幾何。`transform-origin` 釘死 `0 0`：`translate() scale()` 喺 CSS 入面 = 矩陣 T·S，
 * local 原點 (0,0) ↦ (dx, dy)，即係位移**完全唔受 s 影響**，dx/dy 直接就係 layout px。
 * origin 用 center 就要再補返 (s−1)·(w/2, h/2)，計錯一個數就飛歪。
 *
 * uniform scale：**s 由「張卡圖要幾高」決定，唔係由 tile 盒決定** —— tile 盒長寬比由
 * treemap 派（實測 0.55–1.94），跟盒計就會出「一大塊色、中間一張細卡」。飛嗰陣 CSS
 * 已經熄咗 tile 底板（`background-color: transparent !important`），個盒幾扁都冇人見到。
 *
 * 點解「盒中心落 stage 中心」＝「卡中心落 stage 中心」：`.tile-card` 係
 * `left:50%; top:50%; translate(-50%,-50%)`（globals.css），卡中心恆等於盒中心，
 * uniform scale + origin 0 0 之下放大完仲係盒中心。
 */
export function flyGeom(
  tile: { x: number; y: number; w: number; h: number },
  card: { w: number; h: number },
  stage: KioskStage,
): { dx: number; dy: number; s: number } {
  const { FIT, FRAME_CAP, SCALE_CAP } = KIOSK_GEOM;
  /* 卡圖長寬比（PSA/GemRate 圖 429×600 ≈ 0.715）；量唔到就用返呢個數，唔好出 NaN/Infinity */
  const ar = card.w > 0 && card.h > 0 ? card.w / card.h : 0.715;
  const targetCardH = Math.min(
    stage.h * FIT,               // 唔好掂 stage 上下邊
    (stage.w * FIT) / ar,        // 亦唔好掂 stage 左右邊
    stage.frameH * FRAME_CAP,    // owner：唔准超出成個 frame 70% 高
    (stage.frameW * FRAME_CAP) / ar,
  );
  const s = card.h > 0 ? Math.max(1, Math.min(targetCardH / card.h, SCALE_CAP)) : 1;
  return {
    dx: stage.x + stage.w / 2 - (tile.w * s) / 2 - tile.x,
    dy: stage.y + stage.h / 2 - (tile.h * s) / 2 - tile.y,
    s,
  };
}

/* ── DOM helper ──────────────────────────────────────────────────────────── */
function readMs(cs: CSSStyleDeclaration, name: string, fallback: number): number {
  const v = (cs.getPropertyValue(name) || "").trim();
  if (/ms$/.test(v)) return parseFloat(v);
  if (/s$/.test(v)) return parseFloat(v) * 1000;
  return fallback;
}

/* 擺位一律 translate3d，唔改 left/top —— transform 唔計 CLS（原型實測 kiosk 期間 CLS = 0）。 */
function placeBox(node: HTMLElement | null, x: number, y: number, w: number, h: number): void {
  if (!node) return;
  node.style.width = `${Math.round(w)}px`;
  node.style.height = `${Math.round(h)}px`;
  node.style.transform = `translate3d(${Math.round(x)}px,${Math.round(y)}px,0)`;
}

function setOn(node: HTMLElement | null, on: boolean): void {
  if (!node) return;
  if (on) node.setAttribute("data-on", "");
  else node.removeAttribute("data-on");
}

function tileImg(tileEl: HTMLElement): HTMLImageElement | null {
  return tileEl.querySelector<HTMLImageElement>(".tile-card img");
}

/*
 * 換大圖：tile 原尺寸得幾十 px 闊，攞緊 200w；飛大 5–8 倍一定糊。
 * 先 `new Image()` 拉 600w 落 cache 兼 decode()，decode 好先改 tile 個 `sizes`。
 * URL 由 `card.image.variants["600"]` 直接攞（同 tile 自己個 srcSet 係同一個值）——
 * **唔准自己砌 URL 或者 parse srcset 字串**：string surgery 出 404 = console error，
 * 一 error 就撞 C8 gate 嗰條「kiosk 期間零 console error」。
 * 任何情況（冇 600w、decode reject）都唔准令巡遊停低。
 *
 * `alive` 唔係可有可無：await 之後可以已經退咗 kiosk（decode 最多拖 DECODE_CAP_MS=600ms，
 * 而呢個 race 用嘅係裸 `window.setTimeout`，cleanup 嗰個 `later()` 清單清唔到佢）。
 * 2026-08-18 對抗式覆核實測到：用 `page.route` 拖住 `_600.webp`、見到星之後 130ms 就退出，
 * 遲到嘅 `apply()` 喺 cleanup 嘅 `restoreImage()` 之後 73ms 先行，於是嗰格 tile 個 `sizes`
 * 永久停喺 `359px`（該格 `--card-w: 84px`，React 應該派 120px）—— 退咗 kiosk 之後，
 * 一格 84px 闊嘅 tile 一直叫瀏覽器攞 600w 大圖，直到下次 resize 先自己好返。
 * 8 次入面中 6 次（有裝 setAttribute 探針果 4 次全中，冇探針果 4 次中 2 次）。
 * 同一個 predicate 之後 `.then()` 都要用，所以由外面派入嚟，唔喺兩邊各寫一次。
 */
async function upgradeImage(img: HTMLImageElement | null, url: string | undefined, targetW: number, alive: () => boolean): Promise<void> {
  if (!img) return;
  const apply = () => img.setAttribute("sizes", `${Math.round(targetW)}px`);
  if (!url) { apply(); return; }        // 同步路：仲喺 alive，唔使驗
  const pre = new Image();
  pre.decoding = "async";
  pre.src = url;
  try {
    /* race 唔係 await：decode() 唔 settle 嘅話 await 會食咗成條巡遊（見 DECODE_CAP_MS） */
    await Promise.race([
      pre.decode ? pre.decode() : Promise.resolve(),
      new Promise<void>((resolve) => { window.setTimeout(resolve, DECODE_CAP_MS); }),
    ]);
  } catch {
    /* 404 / 爛圖：照飛，只係糊啲 */
  }
  if (!alive()) return;                 // 遲到就唔准寫，留返 restoreImage 派嗰個值
  apply();
}

/*
 * 退返細圖。**唔用 dataset stash**（原型嗰招有真 bug：stash 只寫一次、還原之後唔 delete，
 * resize 之後 React 派咗新 sizes，飛完會被還原做舊值，嗰格之後永遠糊）。
 * 改為由同一個真身重算：`tileImageSizes(--card-w)` 同 React 派嗰條（heatmap.tsx →
 * `tileImageSizes(cardBox.cardW)` → heatmap-tile.tsx `--card-w`）係同一條式、同一個輸入。
 */
function restoreImage(tileEl: HTMLElement | null): void {
  if (!tileEl) return;
  const img = tileImg(tileEl);
  if (!img) return;
  const cardW = parseFloat(tileEl.style.getPropertyValue("--card-w"));
  if (Number.isFinite(cardW)) img.setAttribute("sizes", tileImageSizes(cardW));
}

/* ── Props ───────────────────────────────────────────────────────────────── */
export interface HeatmapKioskFxProps {
  sectionRef: RefObject<HTMLElement | null>;
  frameRef: RefObject<HTMLDivElement | null>;
  /** cardId → tile element（heatmap.tsx 每次 layout effect 換一個新 Map，所以一律 use 嗰刻先讀 .current） */
  tileElsRef: RefObject<Map<string, HTMLButtonElement>>;
  /** treemap 原始格（`item.card` 就係完整 MarketCardView） */
  tiles: { item: { card: MarketCardView } }[];
  /** tile 實際盒（扣 gap + 釘落 device px）—— flyGeom 要嘅係呢個，唔係 treemap 原始盒 */
  tileBoxes: { x: number; y: number; w: number; h: number }[];
  /** frame 闊高（snapFrameGrid 出嗰對，唔好用 gBCR：飛緊嗰陣 frame 入面有 transform） */
  width: number;
  height: number;
  locale: Locale;
  currency: Currency;
  rates: Record<Currency, number>;
  period: MarketWindow;
  /** copy[locale].status.unavailable —— 唔好喺呢度再 import 成個 copy */
  unavailable: string;
  /** 升／跌色，同 tile 同一個來源（heatmap.tsx 個 colors useMemo，已經按 red-up 對調過）。
      CSS 唔准用 --frame-up/--frame-down：嗰對 token data-updown 唔對調，red-up 會出「格紅、板綠」。 */
  moveUp: string;
  moveDown: string;
}

export function HeatmapKioskFx({
  sectionRef, frameRef, tileElsRef, tiles, tileBoxes, width, height,
  locale, currency, rates, period, unavailable, moveUp, moveDown,
}: HeatmapKioskFxProps) {
  const ghostRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const ringRef = useRef<HTMLDivElement>(null);
  const boardRef = useRef<HTMLDivElement>(null);
  const rankRef = useRef<HTMLSpanElement>(null);
  const moveRef = useRef<HTMLElement>(null);
  const nameRef = useRef<HTMLParagraphElement>(null);
  const capRef = useRef<HTMLSpanElement>(null);

  /* 巡遊要用嘅資料一律經 ref 讀：state machine 住喺一個 effect 入面，
     唔想每次 props 變就拆咗成條 timer 鏈重嚟（一輪 5260ms 入面 1600ms 係飛行段，
     拆 data-kiosk-star 嗰刻連 transition-property 都一齊消失 → 飛緊嘅卡會瞬間彈返原位）。 */
  const dataRef = useRef({ tiles, tileBoxes, locale, currency, rates, period, unavailable });
  /* 巡遊進度全部 ref，冇一個係 React state（契約第 4 條）。揸 card id 唔揸 element：React 會重排 DOM node。 */
  const tourRef = useRef({
    timers: [] as number[],
    idx: -1,
    starId: null as string | null,
    mode: "fly" as "fly" | "hold",
    running: false,
  });

  /*
   * 資訊板用 ref 寫 textContent 唔用 React state，兩個理由：
   * ① `armBillboard` 要「拆 attribute → 逼 style 重算 → 再落」先 re-arm 到入場動畫，
   *    用 state 就要 flushSync 或者再開一個 layout effect 夾次序，白白複雜；
   * ② 契約第 4 條寫死「巡遊狀態全部係 attribute」，內容跟住走先唔會有人下次搞錯。
   * React re-render 唔會抹走呢啲 textContent / data-on：佢只 diff 自己派過嘅 props，
   * 同 `data-corner`（heatmap.tsx）一樣道理。
   */
  const fillBillboard = useCallback((card: MarketCardView) => {
    const d = dataRef.current;
    const m = card.windows[d.period].changePct;
    if (rankRef.current) rankRef.current.textContent = String(card.viewRank);
    const nameEl = nameRef.current;
    if (nameEl) {
      nameEl.textContent = displayCardName(card, d.locale, d.unavailable);
      /* undefined = 同頁面語言 → **一定要 removeAttribute，唔准寫空字串**（lang="" 係另一回事） */
      const lang = cardNameLangAttr(card, d.locale);
      if (lang) nameEl.setAttribute("lang", lang);
      else nameEl.removeAttribute("lang");
    }
    const moveEl = moveRef.current;
    if (moveEl) {
      /* formatPercent 自己已經帶 `+` 號，唔准再加；metric 未 ready 會回一段字唔係數 */
      moveEl.textContent = formatPercent(m, d.locale);
      /* 方向行 metricTone，**唔准 regex parse 個 % 字串** —— 遇到「暫無資料」會靜靜變 flat + 空字 */
      const tone = metricTone(m);
      moveEl.setAttribute("data-dir", tone === "positive" ? "up" : tone === "negative" ? "down" : "flat");
    }
    if (capRef.current) {
      capRef.current.textContent = formatMetricMoney(card.marketCap, d.currency, d.rates, d.locale, true);
    }
  }, []);

  /* props → ref。順手重寫當前嗰塊板：期間／語言／貨幣一變，板上面就唔可以仲係舊期間個 %
     （tile 個色已經跟新期間重畫咗 → 綠格配紅數字）。 */
  useEffect(() => {
    dataRef.current = { tiles, tileBoxes, locale, currency, rates, period, unavailable };
    const id = tourRef.current.starId;
    if (!id) return;
    const card = tiles.find((t) => t.item.card.id === id)?.item.card;
    if (card) fillBillboard(card);
  }, [tiles, tileBoxes, locale, currency, rates, period, unavailable, fillBillboard]);

  /* 資訊板升跌色：由 tsx 派，同 tile 同一個來源。CSS 側讀 `var(--kiosk-move-up, var(--frame-up))`。 */
  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    frame.style.setProperty("--kiosk-move-up", moveUp);
    frame.style.setProperty("--kiosk-move-down", moveDown);
    return () => {
      frame.style.removeProperty("--kiosk-move-up");
      frame.style.removeProperty("--kiosk-move-down");
    };
  }, [frameRef, moveUp, moveDown]);

  /*
   * 入場 burst（一次性）。
   * **一定要 `useEffect` 唔可以 `useLayoutEffect`**：`--fx-r` 係 parent（heatmap.tsx）個
   * layout effect 寫嘅，而 React commit 次序係「所有 layout effect（child 先）→ 所有 passive
   * effect」。用 layout effect 就會喺 parent 寫 `--fx-r` 之前 arm →
   * `animation-delay: calc(var(--fx-r, 0) * 520ms)` 全部 fallback 落 0 → 100 格同一時間彈，
   * 冇咗由中心散開嗰個效果。
   * deps 用 [width, height]：要等 `--fx-r` 按全屏尺寸寫過先 arm，唔係 100 格嘅散開半徑
   * 係照舊尺寸算。入 kiosk 個陣 frame 由細變全屏，ResizeObserver 會再派一次 size，
   * 即係呢個 effect 會行多過一次 —— 咁啱就係我哋想要嘅：用最終尺寸重播一次。
   * disarm 一定要 timeout 兜底：reduced-motion 之下 `animationend` 唔會 fire。
   */
  useEffect(() => {
    const section = sectionRef.current;
    if (!section || width <= 0 || height <= 0) return;
    const frame = frameRef.current;
    const cs = frame ? getComputedStyle(frame) : null;
    const delay = cs ? readMs(cs, "--kiosk-burst-delay", BURST_MAX_DELAY) : BURST_MAX_DELAY;
    const dur = cs ? readMs(cs, "--kiosk-burst-dur", BURST_DUR) : BURST_DUR;
    section.removeAttribute("data-kiosk-enter");
    /* 逼一次 style/layout 重算：removeAttribute → setAttribute 喺同一 tick，
       唔逼嘅話瀏覽器見唔到「拆咗」，條 animation 唔會重播。 */
    section.getBoundingClientRect();
    section.setAttribute("data-kiosk-enter", "");
    const timer = window.setTimeout(() => section.removeAttribute("data-kiosk-enter"), delay + dur + BURST_TAIL);
    return () => {
      window.clearTimeout(timer);
      /* 唔拆嘅話 `will-change: transform, opacity` 掛住 100 格，而 disarm timer 已經冇咗 → 永遠拆唔返 */
      section.removeAttribute("data-kiosk-enter");
    };
  }, [sectionRef, frameRef, width, height]);

  /*
   * ── 巡遊 state machine ──────────────────────────────────────────────────
   * deps 只有 [width, height]：幾何真係變咗（resize / 橫轉直）先 restart，
   * 資料換咗（router.refresh 每 5 分鐘）唔 restart —— 每一步開頭都會由 dataRef 讀返最新
   * tiles/tileBoxes，落到下一步自然換身，唔會喺飛行中途拆 attribute 令張卡彈返原位。
   */
  useEffect(() => {
    const frame = frameRef.current;
    const section = sectionRef.current;
    if (!frame || !section || width <= 0 || height <= 0) return;
    /* `tour` kill switch（契約 §2）。
       ⚠️ 2026-08-18 之前呢個 token 喺兩邊都**冇 call site**：CSS 側五個 token 有四個
       （breathe / sweep / edge / noise）各有 rule，唯獨 tour 一條都冇 —— 因為巡遊唔係 CSS
       開嘅，係本 effect 派 `data-kiosk-tour` 開嘅，而本 effect 當時完全冇睇個 token。
       即係話「熄 tour」呢個掣係死嘅。實測（temp/fe05/kiosk-fx-land/perf/probe-method.json
       Q2）：`?kioskfx=`（五個全熄）之下仍然飛咗 3 次、`data-kiosk-tour` 仍然係 "fly"。
       契約寫明「kill switch 一定要保留，店主機頂唔順要熄得」，所以喺呢度補返。
       讀 DOM 唔加 props：token 由 heatmap.tsx `useState(readKioskFx)` 一次過定，成個
       session 都唔會變，所以喺 mount 讀一次啱返佢個生命週期（同 §4「狀態全部係
       attribute」一致）。熄咗之後 `data-kiosk-tour` 由頭到尾唔會出現，上面所有
       `[data-kiosk-tour]` 嘅 rule（連 `.heatmap-dim` 變暗）自然全部唔 match。 */
    if (!(section.getAttribute("data-kiosk-fx") ?? "").split(/\s+/).includes("tour")) return;
    /* 七件 overlay 一開波就抄落 local，成個 effect（連 cleanup）只用呢幾個 local。
       唔喺 cleanup 讀 `.current`：unmount 嗰陣 React 會喺 destroy 前後某一刻 detach ref，
       讀到 null 就靜靜跳過 —— 「有 cleanup code 但根本冇行到」係最難查嗰種漏。
       呢幾個 node 由本 component 自己 render，refs 喺 commit（passive effect 之前）已經接好。 */
    const ghostEl = ghostRef.current;
    const stageEl = stageRef.current;
    const ringEl = ringRef.current;
    const boardEl = boardRef.current;

    const tour = tourRef.current;
    /* 時間單一真身：CSS 出、JS 讀。方向唔准調轉。 */
    const cs = getComputedStyle(frame);
    const flyOut = readMs(cs, "--kiosk-fly-out", 900);
    const flyBack = readMs(cs, "--kiosk-fly-back", 700);
    const reduceQuery = window.matchMedia("(prefers-reduced-motion: reduce)");

    const later = (fn: () => void, ms: number) => { tour.timers.push(window.setTimeout(fn, ms)); };
    const clearTimers = () => {
      tour.timers.forEach((id) => window.clearTimeout(id));
      tour.timers = [];
    };

    /* 拆走上一格：attribute + 三個飛行 property + --star-bg + 退返細圖。
       漏一樣都會出事 —— 第二次入 kiosk 會有兩格帶 star（兩張大卡疊住，舊嗰張永遠唔會飛返），
       大圖唔退就 20 張 600w decoded bitmap 一直唔放。 */
    const clearStar = () => {
      const id = tour.starId;
      tour.starId = null;
      if (!id) return;
      const el = tileElsRef.current.get(id);
      if (!el) return;
      el.removeAttribute("data-fly");
      el.removeAttribute("data-kiosk-star");
      el.style.removeProperty("--fly-x");
      el.style.removeProperty("--fly-y");
      el.style.removeProperty("--fly-s");
      el.style.removeProperty("--star-bg");
      restoreImage(el);
    };

    const allOff = () => {
      setOn(ghostEl, false);
      setOn(stageEl, false);
      setOn(ringEl, false);
      setOn(boardEl, false);
    };

    /* re-arm 資訊板入場動畫：拆 attribute → 逼 style 重算 → 再落 */
    const armBillboard = () => {
      if (!boardEl) return;
      boardEl.removeAttribute("data-on");
      boardEl.getBoundingClientRect();
      boardEl.setAttribute("data-on", "");
    };

    const step = () => {
      if (!tour.running) return;
      const d = dataRef.current;
      const pool = Math.min(TOUR_POOL, d.tiles.length);
      if (pool <= 0) { later(step, GAP_MS); return; } // 板未有格（size 未量到 / 空資料）：唔好死
      const idx = (tour.idx + 1) % pool;
      tour.idx = idx;
      const card = d.tiles[idx]?.item.card;
      const box = d.tileBoxes[idx];
      const el = card ? tileElsRef.current.get(card.id) ?? null : null;
      /* 卡跌出榜（router.refresh 之後 React 已經 unmount 咗個 node）：跳過去下一格，
         唔好留低一團射燈光暈照住一個空位再靜靜死掉。 */
      if (!card || !box || !el || !el.isConnected) {
        clearStar();
        allOff();
        later(step, GAP_MS);
        return;
      }

      if (tour.starId && tour.starId !== card.id) clearStar();
      tour.starId = card.id;
      el.setAttribute("data-kiosk-star", "");

      const stage = stageBox(width, height);
      /* 讀 `el.style` 唔讀 `getComputedStyle`：inline 直讀，零 style recalc。
         `--card-w/--card-h` 由 heatmap-tile.tsx inline 派（snapCardBox 出嘅數）。
         fallback 嗰對 0.88 / 0.86 同 tileCardSize() 嘅上限一致。 */
      const cardW = parseFloat(el.style.getPropertyValue("--card-w")) || box.w * 0.88;
      const cardH = parseFloat(el.style.getPropertyValue("--card-h")) || box.h * 0.86;
      const g = flyGeom(box, { w: cardW, h: cardH }, stage);

      /* 擺位：板同 section 都要（logo 喺 frame 外面，CSS 由板揀唔到佢）。
         判斷只有 stageBox() 一個真身，CSS 唔准再寫多次 1.15。 */
      boardEl?.setAttribute("data-place", stage.place);
      section.setAttribute("data-kiosk-place", stage.place);
      fillBillboard(card);

      /* hold fallback 個光環照擺（reduce sibling 會喺 fly mode 之下叫佢出場） */
      placeBox(
        ringEl,
        box.x - (box.w * 0.06) / 2 - RING_PAD,
        box.y - (box.h * 0.06) / 2 - RING_PAD,
        box.w * 1.06 + 2 * RING_PAD,
        box.h * 1.06 + 2 * RING_PAD,
      );

      if (tour.mode === "hold") {
        setOn(ringEl, true);
        armBillboard();
        later(() => {
          setOn(boardEl, false);
          setOn(ringEl, false);
          clearStar();
          later(step, GAP_MS);
        }, HOLD_MS);
        return;
      }

      /* 個「窿」：擺喺 tile 原位，順手抄埋佢自己嘅熱力色。
         讀 inline（heatmap-tile.tsx `style={{background: p.bg}}`），拎唔到先跌落 computed。
         主題喺飛行中途切換：最多係嗰一步個 ghost 用舊色，下一步自己啱返 —— 唔使特別處理。 */
      const starBg = el.style.backgroundColor || getComputedStyle(el).backgroundColor;
      el.style.setProperty("--star-bg", starBg);
      ghostEl?.style.setProperty("--star-bg", starBg);
      placeBox(ghostEl, box.x, box.y, box.w, box.h);
      /* 射燈：釘喺 stage 正中，大過飛到位嗰張**卡**（唔係 tile 盒）1.7 倍 */
      const glowW = cardW * g.s * 1.7;
      const glowH = cardH * g.s * 1.7;
      placeBox(stageEl, stage.x + stage.w / 2 - glowW / 2, stage.y + stage.h / 2 - glowH / 2, glowW, glowH);

      /* 幾何寫三個 custom property，**唔係** inline transform */
      el.style.setProperty("--fly-x", `${Math.round(g.dx)}px`);
      el.style.setProperty("--fly-y", `${Math.round(g.dy)}px`);
      el.style.setProperty("--fly-s", g.s.toFixed(3));

      /* 起飛前先換大圖，decode 完先飛，所以「飛大之後糊咗一陣」由頭到尾都冇。
         await 之後一定要重驗身份 —— 唔驗嘅話上一步嘅 promise 會喺新一步度 arm 錯格。
         同一個 predicate 要派埋入 upgradeImage：佢個 `apply()` 喺呢個 `.then()` 之前行，
         唔派就會喺退咗 kiosk 之後仲寫一次 `sizes`（見 upgradeImage 上面段註解嘅實測）。 */
      const stillStar = () => tour.running && tour.starId === card.id && el.isConnected;
      void upgradeImage(tileImg(el), card.image.variants?.["600"], cardW * g.s, stillStar).then(() => {
        if (!stillStar()) return;
        el.setAttribute("data-fly", "out");
        setOn(ghostEl, true);
        setOn(stageEl, true);

        later(armBillboard, Math.max(0, flyOut - BOARD_LEAD_MS));
        later(() => setOn(boardEl, false), flyOut + HOLD_MS);
        later(() => {
          el.removeAttribute("data-fly");                 // 飛返原位
          setOn(ghostEl, false);
          setOn(stageEl, false);
        }, flyOut + HOLD_MS + BACK_LEAD_MS);
        later(() => {
          /* 落返地：拆 data-kiosk-star ⇒ CSS 嗰句 will-change 一齊消失（同一時間最多 1 格 promote），
             大圖亦都退返細圖。 */
          clearStar();
          later(step, GAP_MS);
        }, flyOut + HOLD_MS + BACK_LEAD_MS + flyBack);
      });
    };

    /* reduced-motion 中途開／關：CSS sibling 即刻封死 transform，但 JS 都要跟住走，
       否則仍然當自己飛緊 900ms → 資訊板要等到 780ms 先出，中間成秒乜都冇郁，睇落似死機。 */
    const syncMode = () => {
      const next: "fly" | "hold" = reduceQuery.matches ? "hold" : "fly";
      if (next === tour.mode) return;
      tour.mode = next;
      frame.setAttribute("data-kiosk-tour", next);
      clearTimers();
      clearStar();
      allOff();
      later(step, GAP_MS);
    };
    reduceQuery.addEventListener("change", syncMode);

    tour.mode = reduceQuery.matches ? "hold" : "fly";
    tour.running = true;
    frame.setAttribute("data-kiosk-tour", tour.mode);
    later(step, TOUR_WARMUP_MS);

    return () => {
      reduceQuery.removeEventListener("change", syncMode);
      tour.running = false;
      clearTimers();
      /*
       * 次序有意思（同規格 A §6.1 個 list 唔同，我跟規格 C）：
       * **先拆 frame 個 `data-kiosk-tour`** —— `transform` 同 `transition` 兩樣都係由
       * `.heatmap-frame[data-kiosk-tour="fly"] .heatmap-tile[data-kiosk-star][data-fly="out"]`
       * 呢條 rule 出，成條 rule 一唔 match，兩樣一齊消失 ⇒ 張卡即刻彈返原位。
       * 倒轉先拆 `data-fly` 就會播足 700ms 飛返，退出全屏嗰下成版飄。
       */
      frame.removeAttribute("data-kiosk-tour");
      clearStar();
      section.removeAttribute("data-kiosk-place");
      allOff();
      ghostEl?.style.removeProperty("--star-bg");
      /* tour.idx 特登**唔** reset：店主行開又返嚟唔使每 5 分鐘由 #1 重新數過。 */
    };
  }, [frameRef, sectionRef, tileElsRef, fillBillboard, width, height]);

  /*
   * 七件 overlay 全部係 `.heatmap-frame` 嘅 child：
   *  · 座標系 —— ghost/stage/ring 係 absolute，JS 寫嘅係 tile 嘅 offsetParent 座標（= frame）
   *  · `border-radius: inherit` —— edge / noise 跟返 frame 個 --heatmap-frame-radius
   *  · 裁切 —— frame 係 overflow:hidden，飛到中間嗰張卡靠佢兜住
   *  · z 階（frame 有 isolation:isolate）：dim 2 → ghost 3 → stage 4 → 飛緊嗰格 5
   *    → sweep 6 → billboard 7 → noise 8 → edge 9
   * 全部 `aria-hidden`：frame 係 role="listbox"，非 option 嘅 child 唔可以入 a11y tree
   * （tile 個 aria-label 已經一句讀晒同一批資料）。CSS 側全部 pointer-events:none，
   * 唔會食走 tile 嘅 pointer / `.closest(".heatmap-tile")` delegation。
   * sweep / noise / edge 冇 ref：純 CSS 驅動，JS 一世都唔會掂。
   */
  return (
    <>
      <div className="kiosk-ghost" ref={ghostRef} aria-hidden="true" />
      <div className="kiosk-stage" ref={stageRef} aria-hidden="true" />
      <div className="kiosk-ring" ref={ringRef} aria-hidden="true" />
      <div className="kiosk-sweep" aria-hidden="true" />
      <div className="kiosk-noise" aria-hidden="true" />
      <div className="kiosk-edge" aria-hidden="true" />
      <div className="kiosk-billboard" ref={boardRef} aria-hidden="true">
        <p className="kiosk-billboard-rank">
          <span className="kiosk-rank-hash">#</span>
          <span className="kiosk-rank-num" ref={rankRef} />
        </p>
        <strong className="kiosk-billboard-move" ref={moveRef} />
        <p className="kiosk-billboard-name" ref={nameRef} />
        <span className="kiosk-billboard-cap" ref={capRef} />
      </div>
    </>
  );
}
