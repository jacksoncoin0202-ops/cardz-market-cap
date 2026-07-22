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

export function marketMetadata(locale: Locale, title: string, description: string, path = "/"): Metadata {
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
        "x-default": localizedPath(path, "en"),
      },
    },
    openGraph: { title, description, siteName: "CARDZ Market Cap", type: "website", locale },
    twitter: { card: "summary_large_image", title, description },
  };
}

export function defaultMarketMetadata(locale: Locale): Metadata {
  const t = copy[locale];
  return marketMetadata(locale, t.hero.title, t.hero.body, "/");
}
