import type { Metadata } from "next";
import { copy } from "./i18n";
import { normaliseLocale } from "./format";
import type { Locale } from "./types";

export type PageSearchParams = Promise<Record<string, string | string[] | undefined>>;

export async function localeFromSearchParams(searchParams: PageSearchParams): Promise<Locale> {
  const value = (await searchParams).lang;
  return normaliseLocale(Array.isArray(value) ? value[0] : value);
}

function localizedPath(path: string, locale: Locale): string {
  if (locale === "en") return path;
  return `${path}${path.includes("?") ? "&" : "?"}lang=${locale}`;
}

/*
 * `twitter:card` 聲明咗 `summary_large_image`，呢種卡片型式強制要圖，冇圖就出一張空白大卡。
 * Next 嘅 `opengraph-image` file convention 喺呢個 app 靠唔住：每版都有自己嘅
 * `generateMetadata` 回傳 `openGraph`，會冚走由上層繼承落嚟嘅 images（實測 `/` 有圖，
 * `/pokemon` `/watchlist` `/graders/*` `/card/*` 全部 MISSING）。所以 og:image 一律喺呢度
 * 落，呢個係全站唯一嘅 metadata 出口，冇得漏。
 */
const DEFAULT_OG_IMAGE = "/brand/og-light.png";

export function marketMetadata(
  locale: Locale,
  title: string,
  description: string,
  path = "/",
  image: string = DEFAULT_OG_IMAGE,
): Metadata {
  const images = [{ url: image, width: 1200, height: 630, alt: "CardZ Marketcap" }];
  return {
    title,
    description,
    alternates: {
      canonical: localizedPath(path, locale),
      languages: {
        en: localizedPath(path, "en"),
        "zh-Hant": localizedPath(path, "zh-TW"),
        "zh-Hans": localizedPath(path, "zh-CN"),
        ja: localizedPath(path, "ja"),
        ko: localizedPath(path, "ko"),
        "x-default": localizedPath(path, "en"),
      },
    },
    openGraph: { title, description, siteName: "CardZ Marketcap", type: "website", locale, images },
    twitter: { card: "summary_large_image", title, description, images },
  };
}

export function defaultMarketMetadata(locale: Locale): Metadata {
  const t = copy[locale];
  return marketMetadata(locale, t.hero.title, t.hero.body, "/");
}
