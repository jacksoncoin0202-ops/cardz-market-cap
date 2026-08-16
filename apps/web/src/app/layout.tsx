import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import { DocumentLanguage, LangScript, SkipLink, ThemeScript } from "@/components/document-language";
import { Footer, Header } from "@/components/header";
import { AppMotionConfig } from "@/components/motion-config";
import { PUBLIC_SITE_URL } from "@/lib/public-site";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(PUBLIC_SITE_URL),
  title: { default: "CardZ Marketcap", template: "%s | CardZ Marketcap" },
  description: "Art market intelligence for collectible cards.",
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
 */
export const revalidate = 300;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <ThemeScript />
        <LangScript />
      </head>
      <body>
        <AppMotionConfig>
          <Suspense fallback={<a className="skip-link" href="#main">Skip to content</a>}><SkipLink /></Suspense>
          <Suspense fallback={null}><DocumentLanguage /></Suspense>
          <Suspense fallback={<div className="header-fallback" />}><Header /></Suspense>
          <main id="main" tabIndex={-1}>{children}</main>
          <Suspense><Footer /></Suspense>
        </AppMotionConfig>
      </body>
    </html>
  );
}
