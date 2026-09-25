/* 純函數，冇 "use client"：api/og/heatmap/route.tsx（server）都 import 呢度嘅 tileStyle + DEFAULT_TILE，
   分享圖同網站先會係同一條色階。加返 "use client" 嗰陣 route 攞到嘅係 client reference，一 call 就炸。 */
import { displayedChangePct } from "./format";
import type { MarketWindow } from "./types";

/* 熱力圖 label 一位小數。印出來係 0.0% 就當 0：中立、無正負號、無色塊。 */
export const TILE_CHANGE_DECIMALS = 1;

function tileChange(value: number | null): number | null {
  if (value === null || !Number.isFinite(value)) return null;
  return displayedChangePct(value, TILE_CHANGE_DECIMALS);
}

/* 純色框強度編碼：框 = 純紅/綠 fill（面積 = 市值，深淺 = 升跌幅），中間放直向卡 */
export interface TileParams {
  clamp: number;      // 1D change% 到幾多就當最深色（爆色）；長窗乘 WINDOW_CLAMP_SCALE
  gamma: number;      // 誇大/壓細強度曲線
  deadzone: number;   // ±deadzone% 之內當中立（0 = 只有顯示 0.0% 嘅格）
  aMin: number;       // 最淺色透明度
  aMax: number;       // 最深色透明度
  gap: number;        // 格與格之間距離 px
  cardPct: number;    // 中間卡佔 tile 短邊比例（0=唔顯示卡）
  cardAspect: number; // 卡闊:高（Wall Card 直向 = 0.714）
  neutralTile: string;
  upDark: string;     // 升 tile 色（dark mode）
  upLight: string;
  downDark: string;   // 跌 tile 色（dark mode）
  downLight: string;
}

/* 色階 owner 2026-09-23 批改（DESIGN.md「明確非目標」已同步）：以前 gamma 4 + aMin 0.78，
   1%→0.780、3%→0.809、5% 以上全部 1.000 —— 3% 以下肉眼同色，1D 成版一隻綠。
   而家 gamma 1.5 + aMin 0.45：1%→0.50、2%→0.59、3%→0.71、4%→0.84。clamp 5 冇郁，所以 6M 大部分格仍然頂格（09-25 起長窗按 WINDOW_CLAMP_SCALE 放大，見下）。 */
export const DEFAULT_TILE: TileParams = {
  clamp: 5, gamma: 1.5, deadzone: 0, aMin: 0.45, aMax: 1, gap: 3,
  cardPct: 0.62, cardAspect: 0.714, neutralTile: "rgba(138, 133, 120, 0.3)",
  upDark: "#17b576", upLight: "#1b714e",
  downDark: "#dc567c", downLight: "#972646",
};

/* localStorage「cardz-heatmap-params」→ TileParams。persistParams 係成套 params 寫低，
   郁過 /tune 嘅人連 2026-09-23 之前嘅舊預設（gamma 4 / aMin 0.78）都存埋，新預設永遠蓋唔到。
   撞正舊預設值就當冇揀過；自訂色、格距等等照留。壞 JSON 由 caller 嘅 try 接。 */
const LEGACY_DEFAULT = { gamma: 4, aMin: 0.78 } as const;
export function restoreTileParams(raw: string | null): TileParams {
  if (!raw) return DEFAULT_TILE;
  const saved = { ...(JSON.parse(raw) as Partial<TileParams> | null) };
  if (saved.gamma === LEGACY_DEFAULT.gamma) delete saved.gamma;
  if (saved.aMin === LEGACY_DEFAULT.aMin) delete saved.aMin;
  return { ...DEFAULT_TILE, ...saved };
}

export interface TileColors {
  up: string;       // 升 tile hex
  down: string;     // 跌 tile hex
  neutral: string;  // 無升跌 tile 色（原樣用，可以係 rgba）
}

export function tileColors(dark: boolean, p: TileParams): TileColors {
  return { up: dark ? p.upDark : p.upLight, down: dark ? p.downDark : p.downLight, neutral: p.neutralTile };
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

/* 每個窗口自己一個飽和點（owner 2026-09-25：「D 卡升親都唔係 5% 咁小，熱力圖 % 間隔要隔開 D」）。
   以前全部窗口都係 clamp 5% 就頂格：09-25 Top100 實測，7D 58%、30D 80%、90D 87%、180D 91%、365D 90%
   嘅格都係最深色，成版一樣深，睇唔出邊張升／跌得最勁，最深色亦冇晒稀缺性。
   clamp 仍然係 1D 嘅飽和點（/tune 個 slider 郁嘅都係佢），長窗按倍數放大；預設 5% →
   1D 5%、7D 20%、30D 50%、90D 70%、180D 120%、365D 250%，大約係 Top100 嗰個窗口第 95 百分位，
   即係每個窗口得最極端嗰 3–8% 格先頂格。scripts/test-fe-heatmap-tile-color.mjs 守住呢組數同三個 call site。 */
export const WINDOW_CLAMP_SCALE: Record<MarketWindow, number> = {
  "1d": 1, "7d": 4, "30d": 10, "90d": 14, "180d": 24, "365d": 50,
};
export function windowTileParams(p: TileParams, period: MarketWindow): TileParams {
  return { ...p, clamp: p.clamp * WINDOW_CLAMP_SCALE[period] };
}

/* 升跌幅用倍數（log）量，唔係直接用 %：+100%（×2）同 −50%（÷2）一樣深。365D 升可以幾百 %、
   跌最多都係幾十 %，直接用 % 嘅話跌得最傷嗰張都永遠淺過一般升幅。細幅度 log ≈ %：1D ±3% 只差 0.01（−3% 0.72、+3% 0.71）。
   ≤ −100%（壞數）當頂格，唔會出 NaN。 */
function logMagnitude(pct: number): number {
  return pct > -100 ? Math.abs(Math.log1p(pct / 100)) : Infinity;
}

/* 強度 0–1：顯示幅喺 deadzone 內 → 0（中立無色）；過咗 deadzone 之後由 0 重新升到 clamp 爆色 */
export function frameStrength(value: number | null, p: TileParams): number {
  const shown = tileChange(value);
  if (shown === null) return 0;
  if (Math.abs(shown) <= p.deadzone) return 0;
  const floor = Math.log1p(p.deadzone / 100);
  const span = Math.max(Math.log1p(p.clamp / 100) - floor, 0.001);
  return Math.pow(Math.min(Math.max(logMagnitude(shown) - floor, 0), span) / span, p.gamma);
}

/* 飽和度跟強度行：t=0 全灰（保留明暗），t=1 全彩——deadzone 內嘅格就近灰色 */
function scaleSaturation(r: number, g: number, b: number, t: number): [number, number, number] {
  const s = 1 - Math.min(Math.max(t, 0), 1);
  if (s <= 0) return [r, g, b];
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return [r + (lum - r) * s, g + (lum - g) * s, b + (lum - b) * s].map((v) => Math.round(v)) as [number, number, number];
}

export interface TileStyle {
  direction: "up" | "down" | "neutral";
  missing: boolean;      // 呢個窗口冇數（null）。同持平一樣係 neutral 灰，UI 再加斜紋分返開
  bg: string;            // tile 純色 fill
  plate: string | null;  // 升跌 label 底板（LABEL_PLATE，統一深色半透明）；neutral 冇底板
  cardW: number;         // 中間卡闊 px
  cardH: number;         // 中間卡高 px
  showCard: boolean;
  move: string | null;   // 升跌 label
  fontSize: number;      // label 字體 px（隨 tile 縮放）
}

/* 中間卡圖尺寸——tileStyle 同 heatmap 暖圖（預拉要揀同一張 srcset 檔）共用，唔准各自再計。
   卡圖用長邊做基準：橫 tile 唔會得個角落咁細。
   高：長邊 × cardPct；闊：aspect 計完再夾喺 tile 闊 88%／高 86% 內，細 tile 唔會貼死框邊。 */
export function tileCardSize(w: number, h: number, p: TileParams): { cardW: number; cardH: number } {
  const longSide = Math.max(w, h);
  let cardH = longSide * p.cardPct;
  let cardW = cardH * p.cardAspect;
  if (cardW > w * 0.88) { cardW = w * 0.88; cardH = cardW / p.cardAspect; }
  if (cardH > h * 0.86) { cardH = h * 0.86; cardW = cardH * p.cardAspect; }
  return { cardW, cardH };
}

/* 升跌 label 幾何契約——同 globals.css `.tile-move` 逐個數對齊，改一邊必改另一邊：
   離 tile 邊 inset px、左右 padding padX、上下 padding padY、line-height。
   owner 2026-08-17：「唔好食咗啲 percentage」——label 一定要成個字入晒 tile 入面，
   仲要離開左右邊；寧願縮字／去小數／索性唔顯示，都唔准裁字。 */
export const TILE_LABEL = { inset: 4, padX: 3, padY: 1, lineHeight: 1.2, minFont: 8, maxFont: 14 } as const;

/* 升跌 label 底板：統一深色半透明（owner 2026-09-23 批）。以前係 tile 同一隻升／跌 hex 34%，
   疊喺同色格上面等於冇底板：dark mode 5% 綠格白字對比得 2.66。而家兩個 theme × 紅綠對調 × 0.1–10%
   白字對比全部 ≥ 4.5（WCAG AA 細字，scripts/test-fe-heatmap-tile-color.mjs 逐格計）。
   neutral（0.0%）照舊冇底板（owner 2026-08-29：0% 唔應該有色塊）。分享圖 route 行同一個 tileStyle。 */
export const LABEL_PLATE = "rgba(10, 10, 10, 0.4)";

/* label 闊度（每 1px 字體嘅 em 數）—— 純查表，唔量 canvas（2026-08-17 起，FE05 webfont）。
   點解唔再用 canvas measureText：
   ① 全站字體而家係 self-host Inter（src/fonts），Inter 4 default 數字係 proportional，CSS 上 label 靠 body
      `font-variant-numeric: tabular-nums` 先變等闊；canvas measureText 完全睇唔到 tnum（亦睇唔到 .tile-move
      嘅 letter-spacing），量出嚟 vs 真 DOM 嘅比例按字串內容由 0.95 飄到 1.18（"+11.1%" 個 1 特別窄），
      一個 fudge 常數修唔到，label 會爆邊。
   ② canvas 第一次量嗰陣 Inter 可能未到手，量到 fallback（Arial metric）再永久 cache，SSR / CSR 又唔一致。
   查表：字元集只有 [+-]?\d+(\.\d)?% 呢幾種，逐隻對住 Inter Variable 800 + tabular-nums 用 DOM
   getBoundingClientRect ×10 字元實測（dev :3901，DESIGN.md §1.3.1 量度記錄）：
     數字 / + / −  0.6455 em（tnum 令 + − 都同數字等闊）
     .            0.2686 em
     %            1.0293 em
   再加 .tile-move 嘅 letter-spacing 0.01em × 字元數（同 globals.css 綁死）。
   Inter 未到手嘅一刻（swap 前）真身係 Arial-metric fallback，數字 0.556em 窄過表值 → 只會細少少，唔會爆邊。
   換字體 / 改 .tile-move weight 或 letter-spacing 就要重量呢三個數——
   temp/fe05/review-webfont/measure-inter.mjs（headless Chromium 對 dev :3901）出 tileLabelEm800tabular。 */
const LABEL_EM_DIGIT = 0.6455;
const LABEL_EM_DOT = 0.2686;
const LABEL_EM_PERCENT = 1.0293;
const LABEL_EM_OTHER = 0.7; /* 表外字元（理論上冇）：保守當闊 */
const LETTER_SPACING_EM = 0.01;
const labelEmCache = new Map<string, number>();
export function tileLabelEm(text: string): number {
  const cached = labelEmCache.get(text);
  if (cached !== undefined) return cached;
  let em = 0;
  for (const ch of text) {
    em += ch === "." ? LABEL_EM_DOT
      : ch === "%" ? LABEL_EM_PERCENT
      : (ch >= "0" && ch <= "9") || ch === "+" || ch === "-" || ch === "−" ? LABEL_EM_DIGIT
      : LABEL_EM_OTHER;
    em += LETTER_SPACING_EM;
  }
  labelEmCache.set(text, em);
  return em;
}

/* 揀 label 文字 + 字體：先試完整「+295.2%」，唔入就縮字（下限 minFont）；仲唔入就去小數「+295%」再縮；
   都唔得就唔顯示（owner：「睇唔到數字唔緊要，但唔好食咗」）。字體上限跟舊規則 12% 短邊、8–14px。 */
export function fitTileLabel(value: number | null, w: number, h: number): { move: string | null; fontSize: number } {
  const L = TILE_LABEL;
  const shortSide = Math.min(w, h);
  const base = Math.max(L.minFont, Math.min(L.maxFont, Math.round(shortSide * 0.12)));
  const shown = tileChange(value);
  if (shown === null) return { move: null, fontSize: base };
  const availW = w - 2 * L.inset - 2 * L.padX;
  const availH = h - 2 * L.inset - 2 * L.padY;
  const sign = shown > 0 ? "+" : "";
  const full = `${sign}${shown.toFixed(1)}%`;
  const compact = `${sign}${Math.round(shown)}%`;
  const candidates = compact === full ? [full] : [full, compact];
  const maxByHeight = Math.floor(availH / L.lineHeight);
  for (const text of candidates) {
    const fontSize = Math.min(base, Math.floor(availW / tileLabelEm(text)), maxByHeight);
    if (fontSize >= L.minFont) return { move: text, fontSize };
  }
  return { move: null, fontSize: base };
}

/* w/h 係 tile 實際顯示尺寸（px） */
export function tileStyle(value: number | null, w: number, h: number, colors: TileColors, p: TileParams): TileStyle {
  const shown = tileChange(value);
  const t = frameStrength(value, p);
  const alpha = p.aMin + t * (p.aMax - p.aMin);
  const direction: TileStyle["direction"] = shown === null || shown === 0 || Math.abs(shown) <= p.deadzone
    ? "neutral"
    : shown > 0 ? "up" : "down";
  const hex = direction === "up" ? colors.up : colors.down;
  const [r, g, b] = p.deadzone > 0 ? scaleSaturation(...hexToRgb(hex), t) : hexToRgb(hex);
  const bg = direction === "neutral" || t === 0
    ? colors.neutral
    : `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
  const plate = direction === "neutral" ? null : LABEL_PLATE;
  const { cardW, cardH } = tileCardSize(w, h, p);
  /* 門檻放寬：tile 細都照 show 卡圖，保持成版整齊（用戶 2026-07-24 指示） */
  const showCard = p.cardPct > 0 && cardW >= 5 && cardH >= 7;
  const { move, fontSize } = fitTileLabel(value, w, h);
  return { direction, missing: shown === null, bg, plate, cardW, cardH, showCard, move, fontSize };
}

export function changeValue(card: { windows: Record<string, { changePct: { status: string; value: number | null } }> }, period: string): number | null {
  const metric = card.windows[period].changePct;
  const ready = (metric.status === "ready" || metric.status === "stale") && metric.value !== null && Number.isFinite(metric.value);
  return ready ? metric.value : null;
}
