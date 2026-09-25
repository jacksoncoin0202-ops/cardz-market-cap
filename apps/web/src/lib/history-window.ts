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
/*
 * 長時段先至掛返參考點（K 線）—— R6b，owner 2026-08-24。
 *
 * `historyDaily` 只有真成交日，而成交系列嘅最早一點中位數得 74 日大，所以 90d 以上
 * 嘅圖基本上係空嘅。呢度只補**最早一單真成交之前**嗰段深歷史：近段永遠淨係真成交，
 * 唔會出現「K 線點插喺兩單真成交中間」。短時段（1d/7d/30d）一個參考點都唔准入。
 *
 * 呢個係「一個窗要邊幾點」政策嘅一部分，所以同 `pointsForWindow` 住埋一齊：
 * 卡頁同分享圖兩邊都 call 呢一個，唔准各寫一次（AGENTS.md 規矩 13）。
 */
export const REFERENCE_MIN_WINDOW_DAYS = 90;

export function mergeReferenceHistory(
  points: PricePoint[],
  reference: PricePoint[],
  days: number,
): PricePoint[] {
  if (days < REFERENCE_MIN_WINDOW_DAYS || reference.length === 0) return points;
  let earliestSale: number | null = null;
  for (const point of points) {
    const ms = Date.parse(point.at);
    if (!Number.isFinite(ms)) continue;
    if (earliestSale === null || ms < earliestSale) earliestSale = ms;
  }
  const deep = reference.filter((point) => {
    const ms = Date.parse(point.at);
    return Number.isFinite(ms) && (earliestSale === null || ms < earliestSale);
  });
  return deep.length === 0 ? points : [...deep, ...points];
}

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
