import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import { DocumentLanguage, LangScript, SkipLink, ThemeScript } from "@/components/document-language";
import { Footer } from "@/components/header";
import { AppMotionConfig } from "@/components/motion-config";
import { ScrollRestoration } from "@/components/scroll-restoration";
import { SiteHeader } from "@/components/site-header";
import { organizationId, siteOrganization, StructuredData } from "@/components/structured-data";
import { PUBLIC_SITE_URL, SITE_DESCRIPTOR_EN } from "@/lib/public-site";
import { inter } from "@/fonts";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(PUBLIC_SITE_URL),
  title: { default: "CardZ Marketcap", template: "%s | CardZ Marketcap" },
  description: SITE_DESCRIPTOR_EN,
  verification: {
    google: "_fvfNcfJclKIRrKY8HXu98F4eLkb1cKmoxxaV8Fa97g",
  },
};

/*
 * 全站根 JSON-LD（GEO，owner 2026-08-16）：Organization + WebSite 各一個，喺 layout
 * 出一次，所有頁面都繼承到。之前 Organization 淨係喺 Dataset/Product 入面做
 * publisher（一個 `@id` 指住個從來冇定義過嘅節點），而 WebSite 全站零。
 * 呢度嘅 `@id` 一定要行 organizationId()，唔准喺呢個檔手砌 hash（AGENTS 規矩 13）。
 * logo 揀 /icons/icon-512.png：實檔存在（public/icons/），512×512 夠 Google 嘅最低要求。
 */
const siteGraph = {
  "@context": "https://schema.org",
  "@graph": [
    {
      ...siteOrganization(),
      "@id": organizationId(),
      description: SITE_DESCRIPTOR_EN,
      logo: new URL("/icons/icon-512.png", PUBLIC_SITE_URL).toString(),
      knowsAbout: [
        "Pokémon Trading Card Game",
        "One Piece Card Game",
        "PSA 10 graded cards",
        "PSA population reports",
        "Trading card market capitalization",
      ],
    },
    {
      "@type": "WebSite",
      "@id": `${PUBLIC_SITE_URL}/#website`,
      url: `${PUBLIC_SITE_URL}/`,
      name: "CardZ Marketcap",
      description: SITE_DESCRIPTOR_EN,
      publisher: { "@id": organizationId() },
      inLanguage: ["en", "zh-Hant", "zh-Hans", "ja", "ko"],
    },
  ],
};

/* themeColor 兩粒跟 OS 嘅 media meta 係無 JS 時嘅底；有 JS 由 ThemeScript 自己嗰粒 meta 蓋過（跟 data-theme）。 */
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  colorScheme: "light dark",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f7f5" },
    { media: "(prefers-color-scheme: dark)", color: "#101010" },
  ],
};
/*
 * 唔喺 layout 讀 `headers()`：一讀成棵樹變 `ƒ Dynamic` + `Cache-Control: no-store`，
 * 下面嘅 `revalidate = 300` 同各頁自己嘅 ISR 全部失效。
 * `<html lang>` SSR 預設 `en`；`?lang=` 由 LangScript 喺 paint 前改，之後 DocumentLanguage 跟 client locale。
 * `data-theme` / `data-updown` 一樣係 client-only 事實，server 唔出，全部由 ThemeScript paint 前落——
 * 所以 <html> 要 suppressHydrationWarning（lang / data-* 同 server HTML 唔同係預期）。
 * `inter.variable`（--font-inter）必須落 <html> 唔係 <body>：globals.css `--font-sans` 住喺 :root，
 * 個 var 喺 body 先定義嘅話 :root 度解唔到 → 整條 --font-sans invalid → 全站跌 serif（見 src/fonts/index.ts）。
 * ThemeScript / LangScript 唔掂 className，所以 SSR 同 client 個 class 一樣，零 hydration mismatch。
 */
export const revalidate = 300;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={inter.variable} suppressHydrationWarning>
      <head>
        <ThemeScript />
        <LangScript />
        <StructuredData value={siteGraph} />
      </head>
      <body>
        <AppMotionConfig>
          {/* 逐個 history entry 記／還原 scroll 位。要喺 layout（唔係榜頁）：由卡頁撳返上一頁，
              卡頁自己嗰個位一樣要記得住。 */}
          <ScrollRestoration />
          <Suspense fallback={<a className="skip-link" href="#main">Skip to content</a>}><SkipLink /></Suspense>
          <Suspense fallback={null}><DocumentLanguage /></Suspense>
          <Suspense fallback={<div className="header-fallback" />}><SiteHeader /></Suspense>
          <main id="main" tabIndex={-1}>{children}</main>
          <Suspense><Footer /></Suspense>
        </AppMotionConfig>
      </body>
    </html>
  );
}
