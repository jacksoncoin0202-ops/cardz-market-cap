"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";
import type { Locale } from "./types";

/*
 * 升跌顏色慣例（owner 2026-08-16）。
 *
 * owner 決定：**全部 locale 默認「綠升紅跌」**，唔跟語言／地區自動轉；只有 header 設定掣
 * 先可以反轉成「紅升綠跌」（全站生效，包括熱力圖 tile 色）。
 * 偏好存 localStorage `cardz-updown`：`auto`（= green-up）| `green-up` | `red-up`。
 * 解析結果寫落 `<html data-updown="red-up|green-up">`，CSS 靠呢個 attribute 對調
 * `--positive` / `--negative`；heatmap.tsx tileColors 同樣按呢個值對調 up/down。
 *
 * ⚠️ 另一個 pre-paint script（document-language.tsx 側）會用同一條 key、同一套規則
 * 喺 hydrate 前先寫 attribute，避免閃色。改 key / 值 / 規則兩邊要一齊改。
 *
 * 同 use-market-settings.ts 個 theme 一樣：偏好係 client-only 事實，用
 * useSyncExternalStore；server snapshot 固定 `auto`，同 SSR HTML 一致，唔會 hydration mismatch。
 */
export type UpDownPref = "auto" | "green-up" | "red-up";
export type UpDownResolved = Exclude<UpDownPref, "auto">;

export const UPDOWN_KEY = "cardz-updown";
const listeners = new Set<() => void>();

function subscribe(onChange: () => void): () => void {
  window.addEventListener("storage", onChange);
  listeners.add(onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    listeners.delete(onChange);
  };
}

function readPref(): UpDownPref {
  try {
    const stored = window.localStorage.getItem(UPDOWN_KEY);
    if (stored === "green-up" || stored === "red-up") return stored;
  } catch { /* ignore */ }
  return "auto";
}

function serverPref(): UpDownPref {
  return "auto";
}

/* locale 參數保留（call site 唔使改），但唔再影響結果 —— owner 定全球默認綠升。 */
export function resolveUpDown(pref: UpDownPref, _locale?: Locale): UpDownResolved {
  return pref === "red-up" ? "red-up" : "green-up";
}

export function useUpDown(locale?: Locale) {
  const pref = useSyncExternalStore(subscribe, readPref, serverPref);
  const resolved = resolveUpDown(pref, locale);

  useEffect(() => {
    document.documentElement.dataset.updown = resolved;
  }, [resolved]);

  const setPref = useCallback((next: UpDownPref) => {
    try {
      /* `auto` 直接寫落去（唔係 remove），等 pre-paint script 同 DevTools 睇得出用戶揀過 */
      window.localStorage.setItem(UPDOWN_KEY, next);
    } catch { /* ignore */ }
    /* storage event 唔會喺自己 tab fire，要自己叫 subscriber */
    for (const notify of listeners) notify();
  }, []);

  return { resolved, pref, setPref };
}
