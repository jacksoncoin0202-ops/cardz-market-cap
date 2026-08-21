import { FORMAT_SIZES, type ShareFormat } from "./share-destinations";
import {
  DEFAULT_SHARE_RESOLUTION,
  RESOLUTION_LABEL,
  type ShareResolution,
} from "./share-resolution";
import type { StampMode } from "./share-stamp";
import { readShareLang, type ShareLang } from "./share-copy";
import type { MarketWindow } from "./types";

/**
 * Server heatmap download. One HTTP GET — no browser, no local :3900 screenshot.
 *
 * GET /api/og/heatmap?period=7d&show=40&scope=all&format=post&theme=dark&updown=green-up&lang=en
 *
 * Image is Top `show` tiles (default 40). Rank numbers in the caption pack
 * still come from Top 100.
 *
 * ⚠️ Default period is **7d**, not `defaultMarketWindow` (180d). Inheriting the
 * site default is the "wrong day trend" trap.
 */
export const HEATMAP_OG_PATH = "/api/og/heatmap";
export const HEATMAP_OG_DEFAULT_SHOW = 40;
export const HEATMAP_OG_DEFAULT_PERIOD: MarketWindow = "7d";
/*
 * ⚠️ 加新 key 要三處一齊改：呢張表、`heatmapOgSearch()`、route 入面個 cache key。
 * 漏咗 cache key 嗰處係最陰功嗰種 bug —— 兩個唔同參數共用一個檔名，第二個人攞到
 * 第一個人張圖，兩邊都係 200，冇人會發現。
 */
export const HEATMAP_OG_QUERY_KEYS = [
  "period", "show", "scope", "format", "theme", "updown", "lang", "res", "stamp", "tz", "at",
] as const;

export type HeatmapOgScope = "all" | "pokemon" | "one-piece";
export type HeatmapOgUpDown = "green-up" | "red-up";
export type HeatmapOgTheme = "dark" | "light";

export interface HeatmapOgQuery {
  period?: MarketWindow;
  show?: number;
  scope?: HeatmapOgScope;
  format?: ShareFormat;
  theme?: HeatmapOgTheme;
  updown?: HeatmapOgUpDown;
  lang?: ShareLang;
  /** `1080p`（預設，快）／`4k`（真 2×，慢好多，見 `lib/share-resolution.ts` 實測） */
  res?: ShareResolution;
  /** `data` = 資料日（預設，og:image／cron 鏈用）；`now` = 出圖嗰一刻 */
  stamp?: StampMode;
  /** `stamp=now` 先有用。IANA 名，例如 `Asia/Tokyo`。認唔到跌返 UTC。 */
  tz?: string;
  /**
   * `stamp=now` 先有用。epoch ms，釘死個戳嗰一刻，令 retry 每次都行返同一條
   * cache key（4K 一定要 retry，見 `lib/share-stamp.ts` 上面段實測）。
   * 出窗／垃圾 → server 當冇俾，用佢自己而家。
   */
  at?: number;
}

export function heatmapOgScopeFromKind(kind: string): HeatmapOgScope {
  return kind === "pokemon" || kind === "one-piece" ? kind : "all";
}

export function heatmapOgLang(locale: string): ShareLang {
  return readShareLang(locale);
}

export function heatmapOgSearch(opts: HeatmapOgQuery = {}): string {
  const q = new URLSearchParams();
  q.set("period", opts.period ?? HEATMAP_OG_DEFAULT_PERIOD);
  q.set("show", String(opts.show ?? HEATMAP_OG_DEFAULT_SHOW));
  q.set("scope", opts.scope ?? "all");
  q.set("format", opts.format ?? "post");
  q.set("theme", opts.theme ?? "dark");
  q.set("updown", opts.updown ?? "green-up");
  q.set("lang", opts.lang ?? "en");
  /*
   * ⚠️ `res` / `stamp` / `tz` 只喺**唔係預設**嗰陣先寫入 query。
   *
   * HERMES 條 cron 鏈（唔喺呢個 repo）一路都係打冇呢三個 key 嘅 URL，佢攞到嘅嘢
   * 唔准因為我哋加咗新掣而變樣（`share-destinations.ts` 明文寫住呢句）。同時網站
   * 預設嗰條 URL 保持同舊版一模一樣 = cache 唔會因為多咗兩個 key 而全部 miss。
   */
  if (opts.res && opts.res !== DEFAULT_SHARE_RESOLUTION) q.set("res", opts.res);
  if (opts.stamp && opts.stamp !== "data") {
    q.set("stamp", opts.stamp);
    if (opts.tz) q.set("tz", opts.tz);
    if (typeof opts.at === "number" && Number.isFinite(opts.at)) q.set("at", String(opts.at));
  }
  return q.toString();
}

export function heatmapOgPath(opts: HeatmapOgQuery = {}): string {
  return `${HEATMAP_OG_PATH}?${heatmapOgSearch(opts)}`;
}

export function heatmapOgFilename(opts: HeatmapOgQuery = {}): string {
  const period = opts.period ?? HEATMAP_OG_DEFAULT_PERIOD;
  const show = opts.show ?? HEATMAP_OG_DEFAULT_SHOW;
  const scope = opts.scope ?? "all";
  const format = opts.format ?? "post";
  const size = FORMAT_SIZES[format];
  const ratio = size.width > size.height ? "wide" : size.height / size.width > 1.5 ? "9x16" : "4x5";
  /* 1080p 唔加後綴：舊檔名一日出咗街（分享出去、存咗落人哋相簿）就唔好無端改。
     4K 一定要加 —— 同一張圖兩個清晰度落同一個 folder，冇後綴就係互相覆蓋。 */
  const res = opts.res ?? DEFAULT_SHARE_RESOLUTION;
  const suffix = res === DEFAULT_SHARE_RESOLUTION ? "" : `-${RESOLUTION_LABEL[res].toLowerCase()}`;
  return `cardz-heatmap-${scope}-top${show}-${period}-${ratio}${suffix}`;
}
