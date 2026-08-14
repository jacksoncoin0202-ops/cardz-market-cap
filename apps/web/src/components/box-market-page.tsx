"use client";

import { useMemo } from "react";
import { useSearchParams } from "next/navigation";
import { BoxGroupSelector, normaliseBoxScope } from "./box-group-selector";
import { BoxRankings } from "./box-rankings";
import { Provenance } from "./provenance";
import { absolutePublicUrl, StructuredData } from "./structured-data";
import { copy } from "@/lib/i18n";
import { formatDate } from "@/lib/format";
import type { MarketViewSnapshot } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

export function BoxMarketPage({ snapshot }: { snapshot: MarketViewSnapshot }) {
  const { locale, currency, href } = useMarketSettings();
  const params = useSearchParams();
  const t = copy[locale];
  const block = snapshot.sealed ?? null;
  const scope = normaliseBoxScope(params.get("group"));
  const products = useMemo(() => {
    if (!block) return [];
    return scope === "all" ? block.products : block.products.filter((product) => product.group === scope);
  }, [block, scope]);
  const coverage = block
    ? t.box.coverage
      .replace("{priced}", String(block.coverage.priced))
      .replace("{total}", String(block.coverage.total))
    : null;
  const structuredData = {
    "@context": "https://schema.org",
    "@graph": [
      {
        "@type": "Dataset",
        name: t.boxHero.title,
        description: t.boxHero.body,
        dateModified: block?.asOf ?? snapshot.effectiveAt,
        measurementTechnique: "BOX reference price: completed sales first, market reference second, ask floor only as fallback",
        publisher: { "@type": "Organization", name: "CardZ Marketcap", url: absolutePublicUrl(href("/")) },
      },
      {
        "@type": "ItemList",
        name: t.box.boardTitle.replace("{count}", String(products.length)),
        numberOfItems: products.length,
        itemListElement: products.slice(0, 100).map((product) => ({
          "@type": "ListItem",
          position: product.rank,
          name: product.name.en,
          url: absolutePublicUrl(href(`/box/${product.id}`)),
        })),
      },
    ],
  };

  return (
    <div className="page-shell market-page-shell watchlist-page-shell" data-cardz-generation={snapshot.generation}>
      <StructuredData value={structuredData} />
      <section className="hero-section fade-up">
        <p className="section-kicker">{t.boxHero.eyebrow}</p>
        <h1>{t.boxHero.title}</h1>
        <p className="hero-copy">{t.boxHero.body}</p>
        {block && (
          <p className="data-time">
            {coverage} · {t.labels.asOf}: {formatDate(block.asOf, locale)}
          </p>
        )}
      </section>
      {/* 原盒 group filter：裝飾性浮現喺手機會拖慢表單出現，
          手機係主軸，所以浮現限定 ≥981px（同主站 controls 一致）。 */}
      <div className="box-controls fade-up fade-up-desktop">
        <BoxGroupSelector locale={locale} />
      </div>
      {!block ? (
        <p className="empty-state fade-up">{t.box.empty}</p>
      ) : (
        <div className="fade-up">
          <BoxRankings products={products} rates={snapshot.rates} locale={locale} currency={currency} href={href} />
        </div>
      )}
      <Provenance updatedAt={block?.asOf ?? snapshot.effectiveAt} kind="box" />
    </div>
  );
}
