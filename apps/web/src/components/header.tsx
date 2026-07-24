"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Globe2, Moon, Sun } from "lucide-react";
import { SelectControl } from "./select-control";
import { copy } from "@/lib/i18n";
import { currencies, locales, type Currency, type Locale } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";

const localeLabel = { en: "EN", "zh-TW": "繁中", "zh-CN": "简中", ja: "日本語", ko: "한국어" } as const;
const currencyLabel = Object.fromEntries(currencies.map((item) => [item, item])) as Record<Currency, string>;

export function Header() {
  const pathname = usePathname();
  const { locale, currency, theme, update, href } = useMarketSettings();
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
          <span>CARDS</span><strong>Market Cap</strong>
        </Link>
        <nav className="primary-nav" aria-label="Primary">
          {links.map((link) => (
            <Link key={link.path} href={href(link.path)} data-active={pathname === link.path || (link.path.startsWith("/graders") && pathname.startsWith("/graders")) ? "true" : "false"}>
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="market-controls">
          <button
            type="button"
            className="select-control theme-toggle"
            aria-label={theme === "dark" ? t.theme.light : t.theme.dark}
            onClick={() => update({ theme: theme === "dark" ? "light" : "dark" })}
          >
            {theme === "dark"
              ? <Sun aria-hidden="true" className="control-icon" strokeWidth={1.7} />
              : <Moon aria-hidden="true" className="control-icon" strokeWidth={1.7} />}
          </button>
          <SelectControl<Locale>
            value={locale}
            options={locales}
            labels={localeLabel}
            onChange={(item) => update({ locale: item })}
            label="Language"
            icon={<Globe2 aria-hidden="true" className="control-icon" strokeWidth={1.7} />}
          />
          <SelectControl<Currency>
            value={currency}
            options={currencies}
            labels={currencyLabel}
            onChange={(item) => update({ currency: item })}
            label="Currency"
            icon={<span aria-hidden="true">$</span>}
            className="currency-control"
          />
        </div>
      </div>
    </header>
  );
}

export function Footer() {
  const { locale } = useMarketSettings();
  const t = copy[locale];
  return (
    <footer className="site-footer">
      <p>{t.footer}</p>
      <div className="footer-methodology">
        <p className="footer-methodology-title">{t.methodology.title}</p>
        <p>{t.methodology.body}</p>
      </div>
    </footer>
  );
}
