import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import { Suspense } from "react";
import { DocumentLanguage } from "@/components/document-language";
import { Footer, Header } from "@/components/header";
import "./globals.css";

export const metadata: Metadata = {
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://cardz-beta.jacksoncoin0202.workers.dev"),
  title: { default: "CARDZ Market Cap", template: "%s | CARDZ Market Cap" },
  description: "Art market intelligence for collectible cards.",
  icons: { icon: "/icon.svg" },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, colorScheme: "light" };
export const dynamic = "force-dynamic";

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const htmlLanguage = (await headers()).get("x-cardz-html-lang") ?? "en";
  return (
    <html lang={htmlLanguage}>
      <body>
        <Suspense fallback={null}><DocumentLanguage /></Suspense>
        <Suspense fallback={<div className="header-fallback" />}><Header /></Suspense>
        <main>{children}</main>
        <Suspense><Footer /></Suspense>
      </body>
    </html>
  );
}
