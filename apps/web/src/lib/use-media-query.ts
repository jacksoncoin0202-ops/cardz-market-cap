"use client";

import { useSyncExternalStore } from "react";

/*
 * 一條 media query 一個 boolean（SSR-safe）。
 *
 * 同 heatmap.tsx 個 `isMobileTiles`、use-market-settings.ts 個 theme 一模一樣嘅寫法：
 * `getServerSnapshot` 固定 `false`，SSR **同 hydration render** 都行佢，所以 server HTML
 * 同第一次 client render 一定一致（桌面版面），hydrate 完先 sync 去真實 matchMedia 值 ——
 * 唔會 hydration mismatch。
 *
 * `subscribe` / `getSnapshot` 要 **穩定 reference**：每次 render 新造 function 會令
 * useSyncExternalStore 每 render 都退訂再訂閱。所以按 query string cache 住兩個 closure。
 */
const subscribers = new Map<string, (onChange: () => void) => () => void>();
const snapshots = new Map<string, () => boolean>();

function subscriberFor(query: string): (onChange: () => void) => () => void {
  let subscribe = subscribers.get(query);
  if (!subscribe) {
    subscribe = (onChange: () => void) => {
      const media = window.matchMedia(query);
      media.addEventListener("change", onChange);
      return () => media.removeEventListener("change", onChange);
    };
    subscribers.set(query, subscribe);
  }
  return subscribe;
}

function snapshotFor(query: string): () => boolean {
  let read = snapshots.get(query);
  if (!read) {
    read = () => window.matchMedia(query).matches;
    snapshots.set(query, read);
  }
  return read;
}

const serverSnapshot = () => false;

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(subscriberFor(query), snapshotFor(query), serverSnapshot);
}
