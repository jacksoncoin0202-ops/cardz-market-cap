import type { MetadataRoute } from "next";
import { WATCHLIST_PAGE_SIZE } from "@/lib/pagination";
import { loadMarketSnapshot } from "@/lib/server-snapshot";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "https://cardzmarketcap.com";
const locales = {
  en: "",
  "zh-Hant": "lang=zh-TW",
  "zh-Hans": "lang=zh-CN",
  ja: "lang=ja",
  ko: "lang=ko",
  "x-default": "",
} as const;

function withQuery(path: string, query: string): string {
  if (!query) return path;
  return path.includes("?") ? `${path}&${query}` : `${path}?${query}`;
}

function entry(path: string, lastModified: string): MetadataRoute.Sitemap[number] {
  return {
    url: `${siteUrl}${path}`,
    lastModified,
    alternates: {
      languages: Object.fromEntries(
        Object.entries(locales).map(([locale, query]) => [locale, `${siteUrl}${withQuery(path, query)}`]),
      ),
    },
  };
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const snapshot = await loadMarketSnapshot();
  const watchlistPageCount = Math.max(
    Math.ceil(snapshot.watchlist.length / WATCHLIST_PAGE_SIZE),
    1,
  );
  const core = [
    "/",
    "/pokemon",
    "/one-piece",
    ...Array.from({ length: watchlistPageCount }, (_, index) =>
      index === 0 ? "/watchlist" : `/watchlist?page=${index + 1}`,
    ),
  ];
  const cardPaths = [...snapshot.top100, ...snapshot.watchlist].map((card) => `/card/${card.id}`);
  return [...core, ...cardPaths].map((path) => entry(path, snapshot.effectiveAt));
}
