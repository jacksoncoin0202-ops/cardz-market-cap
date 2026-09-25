"use client";

import Link from "next/link";
import { Suspense } from "react";
import { Tagline } from "@/components/tagline";
import { copy } from "@/lib/i18n";
import type { Locale } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

/*
 * 全站 404（`notFound()` 同任何未匹配 route 都落呢度）。
 * not-found 讀唔到 searchParams / cookie（server 側一讀就變 dynamic），所以整版係 client：
 * locale 由 useMarketSettings 睇 URL ?lang（middleware 保證非 en 一定帶住），返回連結亦保留 lang / currency。
 * useSearchParams 要 Suspense；prerender `/_not-found` 嗰陣 fallback 出英文版，唔會空白。
 */
function NotFoundBody({ locale, home }: { locale: Locale; home: string }) {
  const t = copy[locale].notFound;
  return (
    <div className="page-shell not-found">
      <h1>{t.title}</h1>
      <p className="hero-copy">{t.body}</p>
      <Link className="primary-action" href={home}>{t.back}</Link>
      <Tagline slot="404" locale={locale} />
    </div>
  );
}

function LocalisedNotFound() {
  const { locale, href } = useMarketSettings();
  return <NotFoundBody locale={locale} home={href("/")} />;
}

export default function NotFound() {
  return (
    <Suspense fallback={<NotFoundBody locale="en" home="/" />}>
      <LocalisedNotFound />
    </Suspense>
  );
}
