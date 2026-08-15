import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import { DocumentLanguage, LangScript, ThemeScript } from "@/components/document-language";
import { Footer, Header } from "@/components/header";
import { PUBLIC_SITE_URL } from "@/lib/public-site";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(PUBLIC_SITE_URL),
  title: { default: "CardZ Marketcap", template: "%s | CardZ Marketcap" },
  description: "Art market intelligence for collectible cards.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", colorScheme: "light dark" };
/*
 * 唔喺 layout 讀 `headers()`：一讀成棵樹變 `ƒ Dynamic` + `Cache-Control: no-store`，
 * 下面嘅 `revalidate = 300` 同各頁自己嘅 ISR 全部失效。
 * `<html lang>` SSR 預設 `en`；`?lang=` 由 LangScript 喺 paint 前改，之後 DocumentLanguage 跟 client locale。
 */
export const revalidate = 300;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" data-theme="light">
      <head>
        <ThemeScript />
        <LangScript />
      </head>
      <body>
        <Suspense fallback={null}><DocumentLanguage /></Suspense>
        <Suspense fallback={<div className="header-fallback" />}><Header /></Suspense>
        <main>{children}</main>
        <Suspense><Footer /></Suspense>
      </body>
    </html>
  );
}
