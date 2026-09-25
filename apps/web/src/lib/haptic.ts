"use client";

/*
 * 克制式觸覺回饋（owner 2026-08-16 揀「關鍵位先震」）。
 *
 * - Android Chrome：navigator.vibrate。
 * - iOS Safari：冇 Vibration API；`<input type=checkbox switch>` 嗰個 trick 只喺 17.4–26.4 有效、
 *   in-app WebView 唔得、而且淨係 toggle 嗰下先震——所以 iOS 唔做假震動，靠 :active 按壓感 + 動效。
 * - 桌面（hover:hover）／reduced-motion：一律 no-op。
 *
 * 用法：tap.tick()（slider 每 10 格）、tap.edge()（掂到 min/max）、tap.select()（period/lang/sort）、
 *       tap.open()／tap.close()（sheet）、tap.success()／tap.error()（copy/share）。
 * 全部 fire-and-forget，冇 return，冇 throw。
 */

let allowed: boolean | null = null;

function canBuzz(): boolean {
  if (allowed !== null) return allowed;
  if (typeof window === "undefined" || typeof navigator === "undefined") return false;
  const vibrate = typeof navigator.vibrate === "function";
  const coarse = window.matchMedia("(hover: none)").matches;
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  allowed = vibrate && coarse && !reduced;
  return allowed;
}

function buzz(pattern: number | number[]): void {
  if (!canBuzz()) return;
  try { navigator.vibrate(pattern); } catch { /* ignore */ }
}

export const tap = {
  /* slider 每過 10 格一下極輕 tick */
  tick: () => buzz(4),
  /* 掂到 min / max：雙擊感 */
  edge: () => buzz([8, 20, 8]),
  /* period / lang / sort / theme 切換 */
  select: () => buzz(6),
  /* bottom sheet / panel 開 */
  open: () => buzz(8),
  /* sheet / panel 關、拉落收 */
  close: () => buzz(4),
  /* copy / share / watchlist 成功 */
  success: () => buzz(10),
  /* 失敗 */
  error: () => buzz([12, 40, 12]),
} as const;
