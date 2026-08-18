import type { PricePoint } from "./types";

/*
 * 「一個時間窗要邊幾點」嘅唯一實現。
 *
 * 原本淨係活喺 `components/history-chart.tsx`（"use client"）入面。分享圖係
 * server 側 render（`api/og/card/[id]` 個 ImageResponse），import 唔到 client
 * component，所以要有一份純函數；但**唔准**喺分享圖嗰邊另寫一次 —— 同一條問題
 * 兩份 copy = 網頁畫 180 日、分享圖畫另一批日（AGENTS.md 規矩 13）。
 *
 * 抽出嚟之後 `history-chart.tsx` 照樣 import 呢個，行為一個字冇改。
 */
export function pointsForWindow(points: PricePoint[], days: number): PricePoint[] {
  const sorted = points
    .filter((point) => Number.isFinite(Date.parse(point.at)))
    .sort((a, b) => Date.parse(a.at) - Date.parse(b.at));
  const latest = sorted.at(-1);
  if (!latest) return [];
  if (days === 1) return sorted.slice(-2);
  const end = Date.parse(latest.at);
  const cutoff = end - days * 86_400_000;
  const inside = sorted.filter((point) => Date.parse(point.at) >= cutoff);
  const anchor = sorted.filter((point) => Date.parse(point.at) < cutoff).at(-1);
  return anchor ? [anchor, ...inside] : inside;
}
