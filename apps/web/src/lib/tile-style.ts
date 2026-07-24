"use client";

/* 純色框強度編碼：框 = 純紅/綠 fill（面積 = 市值，深淺 = 升跌幅），中間放直向卡 */
export interface TileParams {
  clamp: number;      // change% 到幾多就當最深色（爆色）
  gamma: number;      // 誇大/壓細強度曲線
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
  clamp: 5, gamma: 4, aMin: 0.78, aMax: 1, gap: 3,
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

export function frameStrength(value: number | null, p: TileParams): number {
  if (value === null || !Number.isFinite(value) || value === 0) return 0;
  return Math.pow(Math.min(Math.abs(value), p.clamp) / p.clamp, p.gamma);
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
  const [r, g, b] = hexToRgb(hex);
  const bg = direction === "neutral" ? colors.neutral : `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
  const shortSide = Math.min(w, h);
  let cardH = shortSide * p.cardPct;
  let cardW = cardH * p.cardAspect;
  if (cardW > w * 0.92) { cardW = w * 0.92; cardH = cardW / p.cardAspect; }
  /* 門檻放寬：tile 細都照 show 卡圖，保持成版整齊（用戶 2026-07-24 指示） */
  const showCard = p.cardPct > 0 && cardW >= 5 && cardH >= 7;
  const move = value !== null ? `${value > 0 ? "+" : ""}${value.toFixed(1)}%` : null;
  /* 字體隨 tile 縮放：12% 短邊，上下限 8–14px，保持成版字體一致（用戶 2026-07-24 指示） */
  const fontSize = Math.max(8, Math.min(14, Math.round(shortSide * 0.12)));
  return { direction, bg, cardW, cardH, showCard, move, fontSize };
}

export function changeValue(card: { windows: Record<string, { changePct: { status: string; value: number | null } }> }, period: "1d" | "7d" | "30d"): number | null {
  const metric = card.windows[period].changePct;
  const ready = (metric.status === "ready" || metric.status === "stale") && metric.value !== null && Number.isFinite(metric.value);
  return ready ? metric.value : null;
}
