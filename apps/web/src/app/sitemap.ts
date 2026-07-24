import type { MetadataRoute } from "next";
import { loadMarketSnapshot } from "@/lib/server-snapshot";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "https://cardsmarketcap.com";
const locales = {
  en: "",
  "zh-Hant": "?lang=zh-TW",
  "zh-Hans": "?lang=zh-CN",
  ja: "?lang=ja",
  ko: "?lang=ko",
  "x-default": "",
} as const;

function entry(path: string, lastModified: string): MetadataRoute.Sitemap[number] {
  return {
    url: `${siteUrl}${path}`,
    lastModified,
    alternates: { languages: Object.fromEntries(Object.entries(locales).map(([locale, query]) => [locale, `${siteUrl}${path}${query}`])) },
  };
}

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const snapshot = await loadMarketSnapshot();
  const core = ["/", "/pokemon", "/one-piece", "/watchlist", "/graders/psa", "/graders/bgs", "/graders/cgc", "/graders/sgc"];
  const cardPaths = [...snapshot.top100, ...snapshot.watchlist].map((card) => `/card/${card.id}`);
  return [...core, ...cardPaths].map((path) => entry(path, snapshot.effectiveAt));
}
