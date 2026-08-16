"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
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
  const headerRef = useRef<HTMLElement | null>(null);

  /* 捲落少少就加條 1px 分界陰影，等 header 同內容有層次 */
  useEffect(() => {
    const el = headerRef.current;
    if (!el) return;
    const onScroll = () => {
      el.dataset.scrolled = window.scrollY > 8 ? "true" : "false";
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const links = [
    { path: "/", label: t.nav.all },
    { path: "/pokemon", label: t.nav.pokemon },
    { path: "/one-piece", label: t.nav.onePiece },
    { path: "/watchlist", label: t.nav.watchlist },
    { path: "/box", label: t.nav.box },
  ];

  return (
    <header className="site-header" ref={headerRef}>
      <div className="header-inner">
        <Link className="brand" href={href("/")} aria-label="CardZ Marketcap">
          <img className="brand-logo" src="/brand/logo-cardz-marketcap.png" alt="CardZ Marketcap" />
          <img className="brand-logo brand-logo-dark" src="/brand/logo-cardz-marketcap-dark.png" alt="" />
        </Link>
        <nav className="primary-nav" aria-label="Primary">
          {links.map((link) => (
            <Link
              key={link.path}
              href={href(link.path)}
              data-active={
                link.path === "/box"
                  ? (pathname === "/box" || pathname.startsWith("/box/") ? "true" : "false")
                  : pathname === link.path ? "true" : "false"
              }
            >
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
      <div className="footer-inner">
        <p className="footer-brandline">{t.footer}</p>
        <div className="footer-methodology">
          <p className="footer-methodology-title">{t.methodology.title}</p>
          <p className="footer-methodology-body">{t.methodology.body}</p>
          <p className="footer-byline">{t.provenance.byline}</p>
        </div>
      </div>
    </footer>
  );
}
