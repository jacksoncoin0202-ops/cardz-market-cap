import type { MarketMetric, MarketStatus } from "./schema.js";

const LIVE: readonly MarketStatus[] = ["ready", "stale"];

function live(metric: MarketMetric | undefined): boolean {
  return metric !== undefined && metric.value !== null && LIVE.includes(metric.status);
}

/** Older `asOf` wins: a composed metric can never look fresher than its stalest input. */
function earlier(a: string | null, b: string | null): string | null {
  if (!a) return b;
  if (!b) return a;
  return a <= b ? a : b;
}

/**
 * 市值 = 價 × POP。所以市值變動 **唔係** 價格變動，係兩個變動相乘：
 *
 *     (1 + Δ價/100)(1 + ΔPOP/100) − 1
 *
 * 直接攞價格 `changePct` 當市值 `changePct` 用（`rankings.tsx` 以前做嘅嘢）
 * 會漏咗 POP 嗰截。POP 只升唔跌，所以幅度永遠低估；而當價格跌、POP 升到
 * 蓋得過，乘出嚟由負變正 —— 箭嘴會**指錯方向**，唔止係精度問題。
 *
 * 兩個輸入有一個唔係 ready/stale 就 fail-closed 出 null。**唔准**退返去用
 * 其中一個頂替：頂替就係原本嗰個 bug。
 */
export function composeChangePct(base: MarketMetric | undefined, factor: MarketMetric | undefined): MarketMetric {
  if (!live(base) || !live(factor)) {
    // 兩邊都冇料先叫 accumulating；有一邊明確 unavailable 就係 unavailable。
    const status: MarketStatus =
      base?.status === "unavailable" || factor?.status === "unavailable" ? "unavailable" : "accumulating";
    return { value: null, status, asOf: null };
  }
  const a = base!.value as number;
  const b = factor!.value as number;
  const composed = ((1 + a / 100) * (1 + b / 100) - 1) * 100;
  if (!Number.isFinite(composed)) return { value: null, status: "unavailable", asOf: null };
  return {
    value: Number(composed.toFixed(6)),
    status: base!.status === "stale" || factor!.status === "stale" ? "stale" : "ready",
    asOf: earlier(base!.asOf, factor!.asOf),
  };
}
