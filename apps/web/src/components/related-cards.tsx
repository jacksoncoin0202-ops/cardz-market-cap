import Link from "next/link";
import { displayCardName } from "@/lib/card-name";
import { formatMetricMoney, formatPercent, metricTone } from "@/lib/format";
import { copy } from "@/lib/i18n";
import { fillTemplate, geoCopy, type RelatedCardLink, type RelatedCardsPayload } from "@/lib/related-cards";
import type { Currency, Locale } from "@/lib/types";
import "@/app/styles/card-links.css";

/*
 * 卡頁底部嘅相關卡連結（owner 2026-08-16，GEO internal linking）。
 * 三條軸：同一個 set、市值排名前後、同一角色嘅其他印刷版本。
 *
 * 純顯示，冇 hook —— 資料由 route（server）計好傳落嚟（src/lib/related-cards.ts
 * `relatedCards()`），因為卡頁行嘅係 `singleCardSnapshot`，client 側根本冇其他卡。
 * `href` 由卡頁傳入（`useMarketSettings().href`），所以連結會帶返
 * ?lang/currency/period，唔會撳一撳就跌返英文。
 */
function RelatedRow({
  card,
  locale,
  currency,
  rates,
  href,
}: {
  card: RelatedCardLink;
  locale: Locale;
  currency: Currency;
  rates: Record<Currency, number>;
  href: (path: string) => string;
}) {
  const t = copy[locale];
  const title = displayCardName(card, locale, card.officialName ?? t.status.unavailable);
  const setName = card.setName[locale] || card.setName.en;
  return (
    <li>
      <Link href={href(`/card/${card.id}`)}>
        <span className="related-name">{title}</span>
        <span className="related-meta">
          {setName ? `${setName} · ` : ""}#{card.collectorNumber}
          {card.marketRank >= 1 ? ` · #${card.marketRank}` : ""}
        </span>
        <span className="related-figures">
          <strong>{formatMetricMoney(card.marketCap, currency, rates, locale, true)}</strong>
          <em className={`metric-${metricTone(card.change7d)}`}>{formatPercent(card.change7d, locale)}</em>
        </span>
      </Link>
    </li>
  );
}

function RelatedGroup({
  title,
  cards,
  moreHref,
  moreLabel,
  locale,
  currency,
  rates,
  href,
}: {
  title: string;
  cards: RelatedCardLink[];
  moreHref?: string;
  moreLabel?: string;
  locale: Locale;
  currency: Currency;
  rates: Record<Currency, number>;
  href: (path: string) => string;
}) {
  if (!cards.length) return null;
  return (
    <section className="related-group">
      <header>
        <h2>{title}</h2>
        {moreHref && moreLabel ? <Link href={moreHref}>{moreLabel}</Link> : null}
      </header>
      <ul>
        {cards.map((card) => (
          <RelatedRow key={card.id} card={card} locale={locale} currency={currency} rates={rates} href={href} />
        ))}
      </ul>
    </section>
  );
}

export function RelatedCards({
  related,
  locale,
  currency,
  rates,
  href,
}: {
  related: RelatedCardsPayload;
  locale: Locale;
  currency: Currency;
  rates: Record<Currency, number>;
  href: (path: string) => string;
}) {
  const t = copy[locale];
  const geo = geoCopy[locale];
  const groups = related.sameSet.length + related.nearbyRanks.length + related.otherPrintings.length;
  if (!groups) return null;
  const columnLabel = `${t.labels.marketCap} · ${t.periods["7d"]} ${t.labels.change}`;
  return (
    <aside className="related-strip" aria-label={geo.relatedRegion}>
      {/* 每格右邊嗰兩個數係「市值 · 7d 變動」，欄名喺區塊頂講一次就夠，
          唔使每一行都重複（手機會迫爆）。 */}
      <p className="related-legend muted-copy">{columnLabel}</p>
      <RelatedGroup
        title={geo.sameSet}
        cards={related.sameSet}
        moreHref={href(related.setPath)}
        moreLabel={geo.viewSet}
        locale={locale}
        currency={currency}
        rates={rates}
        href={href}
      />
      <RelatedGroup
        title={geo.nearbyRanks}
        cards={related.nearbyRanks}
        locale={locale}
        currency={currency}
        rates={rates}
        href={href}
      />
      <RelatedGroup
        title={fillTemplate(geo.otherPrintings, { name: related.characterLabel ?? "" })}
        cards={related.characterLabel ? related.otherPrintings : []}
        locale={locale}
        currency={currency}
        rates={rates}
        href={href}
      />
    </aside>
  );
}
