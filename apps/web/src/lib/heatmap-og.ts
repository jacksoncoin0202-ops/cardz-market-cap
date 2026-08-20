import { FORMAT_SIZES, type ShareFormat } from "./share-destinations";
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
export const HEATMAP_OG_QUERY_KEYS = ["period", "show", "scope", "format", "theme", "updown", "lang"] as const;

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
  return `cardz-heatmap-${scope}-top${show}-${period}-${ratio}`;
}
