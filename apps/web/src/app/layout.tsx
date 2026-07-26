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
export const dynamic = "force-dynamic";

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
