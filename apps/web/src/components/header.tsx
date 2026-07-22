"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Globe2 } from "lucide-react";
import { copy } from "@/lib/i18n";
import { currencies, locales } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

const localeLabel = { en: "EN", "zh-TW": "繁中", "zh-CN": "简中", ja: "日本語" } as const;

export function Header() {
  const pathname = usePathname();
  const { locale, currency, update, href } = useMarketSettings();
  const t = copy[locale];
  const links = [
    { path: "/", label: t.nav.all },
    { path: "/pokemon", label: t.nav.pokemon },
    { path: "/one-piece", label: t.nav.onePiece },
    { path: "/graders/psa", label: t.nav.graders },
    { path: "/watchlist", label: t.nav.watchlist },
  ];

  return (
    <header className="site-header">
      <div className="header-inner">
        <Link className="brand" href={href("/")}>
          <span>CARDZ</span><strong>Market Cap</strong>
        </Link>
        <nav className="primary-nav" aria-label="Primary">
          {links.map((link) => (
            <Link key={link.path} href={href(link.path)} data-active={pathname === link.path || (link.path.startsWith("/graders") && pathname.startsWith("/graders")) ? "true" : "false"}>
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="market-controls">
          <label className="select-control">
            <span className="sr-only">Language</span>
            <Globe2 aria-hidden="true" className="control-icon" strokeWidth={1.7} />
            <select value={locale} onChange={(event) => update({ locale: event.target.value as typeof locale })}>
              {locales.map((item) => <option key={item} value={item}>{localeLabel[item]}</option>)}
            </select>
          </label>
          <label className="select-control currency-control">
            <span aria-hidden="true">$</span>
            <span className="sr-only">Currency</span>
            <select value={currency} onChange={(event) => update({ currency: event.target.value as typeof currency })}>
              {currencies.map((item) => <option key={item} value={item}>{item}</option>)}
            </select>
          </label>
        </div>
      </div>
    </header>
  );
}

export function Footer() {
  const { locale } = useMarketSettings();
  return <footer className="site-footer"><p>{copy[locale].footer}</p></footer>;
}
