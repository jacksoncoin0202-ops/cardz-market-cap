"use client";

import { useSyncExternalStore } from "react";
import { pickTagline } from "@/lib/taglines";
import type { Locale } from "@/lib/types";

/* 唔會更新，純粹等 useSyncExternalStore 分 SSR/client snapshot */
const subscribe = () => () => {};

/* Prestige tagline：SSR/首 paint 出 nbsp 佔位（server snapshot，穩定 layout、冇 hydration mismatch），
   client 先按 slot + 當日 UTC date 出句（pickTagline deterministic，每日輪換）。 */
export function Tagline({ slot, locale }: { slot: string; locale: Locale }) {
  const text = useSyncExternalStore(
    subscribe,
    () => pickTagline(locale, slot),
    () => "",
  );
  return <p className="prestige-tagline">{text || "\u00A0"}</p>;
}
