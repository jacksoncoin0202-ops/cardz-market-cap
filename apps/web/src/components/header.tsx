"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Globe2, Moon, Sun } from "lucide-react";
import { SelectControl } from "./select-control";
import { SiteSearch } from "./site-search";
import { tap } from "@/lib/haptic";
import { copy } from "@/lib/i18n";
import { currencies, locales, type Currency, type Locale } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";
import { useUpDown } from "@/lib/use-updown";

const localeLabel = { en: "EN", "zh-TW": "繁中", "zh-CN": "简中", ja: "日本語", ko: "한국어" } as const;
/* 選項文字係各自語言寫嘅，俾 lang tag 讀屏先唔會用英文口音讀「日本語」 */
const localeLang = { en: "en", "zh-TW": "zh-Hant", "zh-CN": "zh-Hans", ja: "ja", ko: "ko" } as const;
const currencyLabel = Object.fromEntries(currencies.map((item) => [item, item])) as Record<Currency, string>;

/*
 * Logo 兩張 PNG（light 879×380、dark 858×348）以前一齊 render、靠 CSS display 切換，
 * 結果 light mode 都會 fetch 埋張 dark logo。而家只 render 一張，src 由 theme 揀
 * （theme 本身已經係 useSyncExternalStore：server / hydration 出 light，之後先換）。
 * width/height 跟真實 PNG 比例寫死，等瀏覽器一早知道個框，唔會 layout shift。
 */
const logoByTheme = {
  light: { src: "/brand/logo-cardz-marketcap.png", width: 879, height: 380 },
  dark: { src: "/brand/logo-cardz-marketcap-dark.png", width: 858, height: 348 },
} as const;

export function Header() {
  const pathname = usePathname();
  const { locale, currency, theme, update, href } = useMarketSettings();
  const { resolved: updown, setPref: setUpDown } = useUpDown(locale);
  const t = copy[locale];
  const headerRef = useRef<HTMLElement | null>(null);
  const reduceMotion = useReducedMotion();

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
    { path: "/box", label: t.nav.box },
  ];

  const logo = logoByTheme[theme];
  const upDownLabel = updown === "red-up" ? t.labels.upDownRed : t.labels.upDownGreen;
  /* 同一粒掣 render 兩次（header 控制列 / nav 行），CSS 按斷點只顯示一粒；display:none 嗰粒唔入 a11y tree。 */
  const upDownButton = (placement: "updown-toggle-header" | "updown-toggle-nav") => (
    <button
      type="button"
      className={`select-control updown-toggle ${placement}`}
      data-updown={updown}
      aria-label={upDownLabel}
      title={upDownLabel}
      onClick={() => {
        tap.select();
        /* 明確揀 green-up / red-up，唔會退返去 auto —— 用戶撳過就係佢嘅選擇 */
        setUpDown(updown === "red-up" ? "green-up" : "red-up");
      }}
    >
      {/* owner 2026-08-17：兩支箭嘴跟現時升跌顏色（上箭 --positive、下箭 --negative），
          data-updown 一反轉顏色即跟。lucide ArrowDownUp 係一條 path 上唔到兩隻色，所以手寫同一幾何。 */}
      <svg aria-hidden="true" className="control-icon updown-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
        <g className="updown-icon-down"><path d="m3 16 4 4 4-4" /><path d="M7 20V4" /></g>
        <g className="updown-icon-up"><path d="m21 8-4-4-4 4" /><path d="M17 4v16" /></g>
      </svg>
    </button>
  );

  return (
    <header className="site-header" ref={headerRef}>
      <div className="header-inner">
        <Link className="brand" href={href("/")} aria-label="CardZ Marketcap">
          {/* inline display 係擋住舊 `[data-theme="dark"] .brand-logo { display:none }` 規則（CSS agent 拆緊） */}
          <img className="brand-logo" src={logo.src} width={logo.width} height={logo.height} alt="CardZ Marketcap" style={{ display: "block" }} />
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
          {/* ≤980 兩行 header：升跌反轉掣搬落 nav 行右邊（CSS 切換邊個顯示），
              第一行淨返 logo + search / theme / 語言 / 貨幣，logo 唔再俾五粒掣逼細。 */}
          {upDownButton("updown-toggle-nav")}
        </nav>
        <div className="market-controls">
          {/* owner 2026-08-17：search 永遠喺 header（排行頁以前收埋，因為頁內有 explore bar；
              「/」快捷鍵仍然讓畀 explore bar，見 site-search.tsx）。 */}
          <SiteSearch />
          <button
            type="button"
            className="select-control theme-toggle"
            aria-label={theme === "dark" ? t.theme.light : t.theme.dark}
            title={theme === "dark" ? t.theme.light : t.theme.dark}
            onClick={() => {
              tap.select();
              update({ theme: theme === "dark" ? "light" : "dark" });
            }}
          >
            {/* Sun / Moon 交叉淡入 + 旋轉（同 copy-button 一樣行 AnimatePresence，唔靠 CSS） */}
            <AnimatePresence mode="wait" initial={false}>
              <motion.span
                key={theme}
                className="theme-toggle-icon"
                initial={{ opacity: 0, rotate: -60, scale: 0.7 }}
                animate={{ opacity: 1, rotate: 0, scale: 1 }}
                exit={{ opacity: 0, rotate: 60, scale: 0.7 }}
                transition={{ duration: reduceMotion ? 0 : 0.2, ease: [0.16, 1, 0.3, 1] }}
              >
                {theme === "dark"
                  ? <Sun aria-hidden="true" className="control-icon" strokeWidth={1.7} />
                  : <Moon aria-hidden="true" className="control-icon" strokeWidth={1.7} />}
              </motion.span>
            </AnimatePresence>
          </button>
          {upDownButton("updown-toggle-header")}
          <SelectControl<Locale>
            value={locale}
            options={locales}
            labels={localeLabel}
            optionLang={localeLang}
            onChange={(item) => update({ locale: item })}
            label={t.labels.language}
            icon={<Globe2 aria-hidden="true" className="control-icon" strokeWidth={1.7} />}
          />
          <SelectControl<Currency>
            value={currency}
            options={currencies}
            labels={currencyLabel}
            onChange={(item) => update({ currency: item })}
            label={t.labels.currency}
            icon={<span aria-hidden="true">$</span>}
            className="currency-control"
          />
        </div>
      </div>
    </header>
  );
}

export function Footer() {
  const { locale, href } = useMarketSettings();
  const t = copy[locale];
  /*
   * GEO 批七版新頁嘅唯一站內入口（verify pass 2026-08-16）。之前佢哋只互相 link
   * 同埋出現喺 sitemap／llms.txt，由主站行唔到過去 —— 爬蟲當孤兒頁，內部連結權重係零。
   * 全部行 href()，所以 ?lang / currency 跟住走，唔會撳一下就跌返英文。
   */
  const exploreLinks = [
    { path: "/rankings", label: t.footerNav.rankings },
    { path: "/market-report", label: t.footerNav.marketReport },
    { path: "/methodology", label: t.footerNav.methodology },
    { path: "/about", label: t.footerNav.about },
    { path: "/faq", label: t.footerNav.faq },
    { path: "/glossary", label: t.footerNav.glossary },
    { path: "/data", label: t.footerNav.data },
  ];
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
      <nav className="footer-nav" aria-label={t.footerNav.heading}>
        <p className="footer-nav-title">{t.footerNav.heading}</p>
        <ul className="footer-nav-list">
          {exploreLinks.map((link) => (
            <li key={link.path}>
              <Link href={href(link.path)}>{link.label}</Link>
            </li>
          ))}
        </ul>
      </nav>
    </footer>
  );
}
