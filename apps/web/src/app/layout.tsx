import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import { Suspense } from "react";
import { DocumentLanguage, ThemeScript } from "@/components/document-language";
import { Footer, Header } from "@/components/header";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://cardzmarketcap.com"),
  title: { default: "CardZ Marketcap", template: "%s | CardZ Marketcap" },
  description: "Art market intelligence for collectible cards.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, viewportFit: "cover", colorScheme: "light dark" };
/*
 * layout 嘅 `force-dynamic` 會蓋過每一版自己嘅 revalidate，所以要一齊解除。
 * 但下面個 `headers()` 仍然令成棵樹 dynamic render（實測：拆走佢 `/tune` 即刻變
 * `○ Static · Revalidate 5m`，唔拆就 `ƒ Dynamic` + `Cache-Control: no-store`），
 * 所以呢個 revalidate 現階段未生效。
 */
export const revalidate = 300;

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const htmlLanguage = (await headers()).get("x-cardz-html-lang") ?? "en";
  return (
    <html lang={htmlLanguage} data-theme="light">
      <head>
        <ThemeScript />
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
