"use client";

/* 純色框強度編碼：框 = 純紅/綠 fill（面積 = 市值，深淺 = 升跌幅），中間放直向卡 */
export interface TileParams {
  clamp: number;      // change% 到幾多就當最深色（爆色）
  gamma: number;      // 誇大/壓細強度曲線
  deadzone: number;   // ±deadzone% 之內當中立：褪色近灰（0 = 關閉）
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

export const DEFAULT_TILE: TileParams = {
  clamp: 5, gamma: 4, deadzone: 0, aMin: 0.78, aMax: 1, gap: 3,
  cardPct: 0.62, cardAspect: 0.714, neutralTile: "rgba(138, 133, 120, 0.3)",
  upDark: "#17b576", upLight: "#1b714e",
  downDark: "#dc567c", downLight: "#972646",
};

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

/* 強度 0–1：|value| 喺 deadzone 內 → 0（近灰）；過咗 deadzone 之後由 0 重新升到 clamp 爆色 */
export function frameStrength(value: number | null, p: TileParams): number {
  if (value === null || !Number.isFinite(value)) return 0;
  const mag = Math.abs(value);
  if (mag <= p.deadzone && p.deadzone > 0) return 0;
  const span = Math.max(p.clamp - p.deadzone, 0.1);
  return Math.pow(Math.min(Math.max(mag - p.deadzone, 0), span) / span, p.gamma);
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
  bg: string;            // tile 純色 fill
  cardW: number;         // 中間卡闊 px
  cardH: number;         // 中間卡高 px
  showCard: boolean;
  move: string | null;   // 升跌 label
  fontSize: number;      // label 字體 px（隨 tile 縮放）
}

/* w/h 係 tile 實際顯示尺寸（px） */
export function tileStyle(value: number | null, w: number, h: number, colors: TileColors, p: TileParams): TileStyle {
  const t = frameStrength(value, p);
  const alpha = p.aMin + t * (p.aMax - p.aMin);
  const direction: TileStyle["direction"] = value !== null && value > 0 ? "up" : value !== null && value < 0 ? "down" : "neutral";
  const hex = direction === "up" ? colors.up : colors.down;
  const [r, g, b] = p.deadzone > 0 ? scaleSaturation(...hexToRgb(hex), t) : hexToRgb(hex);
  const bg = direction === "neutral" || (p.deadzone > 0 && t === 0)
    ? colors.neutral
    : `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
  const shortSide = Math.min(w, h);
  /* 卡圖用長邊做基準：橫 tile 唔會得個角落咁細。
     高：長邊 × cardPct；闊：aspect 計完再夾喺 tile 闊 92% 內。 */
  const longSide = Math.max(w, h);
  let cardH = longSide * p.cardPct;
  let cardW = cardH * p.cardAspect;
  /* 闊高雙夾，留最少 6% 邊，細 tile 唔會貼死框邊 */
  if (cardW > w * 0.88) { cardW = w * 0.88; cardH = cardW / p.cardAspect; }
  if (cardH > h * 0.86) { cardH = h * 0.86; cardW = cardH * p.cardAspect; }
  /* 門檻放寬：tile 細都照 show 卡圖，保持成版整齊（用戶 2026-07-24 指示） */
  const showCard = p.cardPct > 0 && cardW >= 5 && cardH >= 7;
  const move = value !== null ? `${value > 0 ? "+" : ""}${value.toFixed(1)}%` : null;
  /* 字體隨 tile 縮放：12% 短邊，上下限 8–14px，保持成版字體一致（用戶 2026-07-24 指示） */
  const fontSize = Math.max(8, Math.min(14, Math.round(shortSide * 0.12)));
  return { direction, bg, cardW, cardH, showCard, move, fontSize };
}

export function changeValue(card: { windows: Record<string, { changePct: { status: string; value: number | null } }> }, period: string): number | null {
  const metric = card.windows[period].changePct;
  const ready = (metric.status === "ready" || metric.status === "stale") && metric.value !== null && Number.isFinite(metric.value);
  return ready ? metric.value : null;
}
