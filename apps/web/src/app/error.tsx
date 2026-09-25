"use client";

import Link from "next/link";
import { Suspense, useEffect } from "react";
import { Tagline } from "@/components/tagline";
import { copy } from "@/lib/i18n";
import type { Locale } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

/*
 * Route-level error boundary（root layout 之下）：任何 page / segment 掟出嚟嘅 runtime error 落呢度，
 * header / footer 照留。同 not-found 共用 `.not-found` 版式；`reset()` 重試 segment，唔使整頁 reload。
 * useSearchParams 要 Suspense，fallback 出英文。
 */
function ErrorBody({ locale, home, reset }: { locale: Locale; home: string; reset: () => void }) {
  const t = copy[locale].errorPage;
  return (
    <div className="page-shell not-found error-page">
      <h1>{t.title}</h1>
      <p className="hero-copy">{t.body}</p>
      <div className="error-page-actions">
        <button type="button" className="primary-action" onClick={reset}>{t.retry}</button>
        <Link className="primary-action error-page-home" href={home}>{copy[locale].notFound.back}</Link>
      </div>
      <Tagline slot="404" locale={locale} />
    </div>
  );
}

function LocalisedError({ reset }: { reset: () => void }) {
  const { locale, href } = useMarketSettings();
  return <ErrorBody locale={locale} home={href("/")} reset={reset} />;
}

export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  useEffect(() => {
    // 唔食咗個 error：dev overlay / 生產 log 都要見到 digest。
    console.error(error);
  }, [error]);
  return (
    <Suspense fallback={<ErrorBody locale="en" home="/" reset={reset} />}>
      <LocalisedError reset={reset} />
    </Suspense>
  );
}
