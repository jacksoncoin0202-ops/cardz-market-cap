"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef } from "react";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { Globe2, Moon, Sun } from "lucide-react";
import { SelectControl } from "./select-control";
import { SiteSearch } from "./site-search";
import { tap } from "@/lib/haptic";
import { currencyDisplayName, currencyMenuGroup, currencyMenuOrder, currencySymbol } from "@/lib/currency-meta";
import { htmlLang, type HtmlLang } from "@/lib/card-name";
import { copy } from "@/lib/i18n";
import { currencies, locales, type Currency, type Locale } from "@/lib/types";
import { useMarketSettings } from "@/lib/use-market-settings";
import { useUpDown } from "@/lib/use-updown";

const localeLabel = { en: "EN", "zh-TW": "繁中", "zh-CN": "简中", ja: "日本語", ko: "한국어" } as const;
/* 選項文字係各自語言寫嘅，俾 lang tag 讀屏先唔會用英文口音讀「日本語」 */
/* 語言選單每個選項用自己嘅語言標 lang（「繁中」要 zh-Hant 字形，唔係跟緊住頁面語言）。
   值一律由 htmlLang() 出 —— 全站得一個 locale → BCP47 嘅真身，唔准喺呢度再抄一份對照表
   （抄一份 = 將來加語言時兩邊會唔同步；scripts/test-fe-lang-attr.mjs 守住）。 */
const localeLang: Record<Locale, HtmlLang> = {
  en: htmlLang("en"),
  "zh-TW": htmlLang("zh-TW"),
  "zh-CN": htmlLang("zh-CN"),
  ja: htmlLang("ja"),
  ko: htmlLang("ko"),
};
/* Trigger 出 code（USD / HKD…）—— 符號行 icon slot，名只喺選單度出。 */
const currencyLabel = Object.fromEntries(currencies.map((item) => [item, item])) as Record<Currency, string>;

/*
 * Logo 兩張 PNG 以前一齊 render、靠 CSS display 切換，結果 light mode 都會 fetch 埋張
 * dark logo。而家只 render 一張，src 由 theme 揀（theme 本身已經係 useSyncExternalStore：
 * server / hydration 出 light，之後先換）。
 * width/height 跟真實 PNG 比例寫死，等瀏覽器一早知道個框，唔會 layout shift。
 *
 * **header 用 `-h100` 細版**（FE05 assets commit）：`.brand-logo` 最高得 50px
 * （手機 34、預設 46、≥1440 50），派原圖 879×380 即係 8.8 倍像素。細版由原圖 lanczos3
 * 縮到高 100px（最大顯示 50px 嘅 2×，Retina 夠用）：light 18,429 → 9,338 B、
 * dark 9,775 → 7,592 B。
 * **原圖唔准刪**：`api/og/card/[id]/route.tsx` 用 sharp 讀原圖砌 1200×630 OG 圖，
 * `lib/share-image.ts` 喺 2496px 闊嘅 canvas 度畫佢 —— 兩處都要全解析度。
 */
const logoByTheme = {
  light: { src: "/brand/logo-cardz-marketcap-h100.png", width: 231, height: 100 },
  dark: { src: "/brand/logo-cardz-marketcap-dark-h100.png", width: 247, height: 100 },
} as const;

/*
 * `availableCurrencies` 由 server wrapper（`site-header.tsx`）落，係「今日 snapshot 真係有匯率」嗰批。
 * 唔傳（例如 storybook / 舊 call site）就當全部有。
 */
export function Header({ availableCurrencies = currencies }: { availableCurrencies?: readonly Currency[] } = {}) {
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
  /* 現時選咗嘅貨幣一定要揀得返（就算佢今日冇匯率），否則個 select 個值唔喺 options 入面。 */
  const currencyOptions = currencyMenuOrder(
    availableCurrencies.includes(currency) ? availableCurrencies : [...availableCurrencies, currency],
  );
  const currencyRegionLabels = {
    asia: t.labels.currencyRegionAsia,
    americas: t.labels.currencyRegionAmericas,
    europe: t.labels.currencyRegionEurope,
    mea: t.labels.currencyRegionMea,
  };
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
            options={currencyOptions}
            labels={currencyLabel}
            onChange={(item) => update({ currency: item })}
            label={t.labels.currency}
            /* 符號 = 貨幣自己個 logo，跟住揀咗邊隻換。<bdi> 隔開阿拉伯文符號（د.إ / ﷼），
               唔隔就會反轉成行嘅視覺次序。 */
            icon={<span aria-hidden="true" className="currency-symbol"><bdi>{currencySymbol[currency]}</bdi></span>}
            className="currency-control"
            menuClassName="currency-menu"
            /* USD 釘頂、冇 heading（null）；其餘按地區分組 */
            groupOf={currencyMenuGroup}
            groupLabels={currencyRegionLabels}
            renderOption={(item) => {
              const name = currencyDisplayName(item, locale);
              return (
                <>
                  <span className="currency-symbol"><bdi>{currencySymbol[item]}</bdi></span>
                  <span className="currency-code">{item}</span>
                  {name && <span className="currency-name">{name}</span>}
                </>
              );
            }}
          />
        </div>
      </div>
    </header>
  );
}

function parrotPathLocale(locale: string, currency: string): "hk" | "tw" | "cn" | "en" {
  if (currency === "HKD") return "hk";
  if (locale === "zh-CN" || currency === "CNY") return "cn";
  if (locale === "zh-TW" || currency === "TWD") return "tw";
  return "en";
}

export function Footer() {
  const { locale, currency, href } = useMarketSettings();
  const t = copy[locale];
  const parrotLocale = parrotPathLocale(locale, currency);
  const parrotBase = `https://parrottcg.com/${parrotLocale}`;
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
  /* Owned content hub — visible + crawlable footer cluster, same tab, no nofollow. */
  const submitLabel = {
    tw: "台灣 PSA 送評",
    hk: "香港 PSA 送評",
    cn: "PSA 送评",
    en: "PSA submit guide",
  }[parrotLocale];
  const lookupLabel = {
    tw: "PSA 10 查價",
    hk: "PSA 10 查價",
    cn: "PSA 10 查价",
    en: "PSA 10 price lookup",
  }[parrotLocale];
  const relatedLinks = [
    { href: `${parrotBase}/`, label: t.relatedNav.hub },
    { href: `${parrotBase}/guides/psa-submit/`, label: submitLabel },
    { href: `${parrotBase}/guides/psa-10-price-lookup/`, label: lookupLabel },
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
      <nav className="footer-nav footer-nav-related" aria-label={t.relatedNav.heading}>
        <p className="footer-nav-title">{t.relatedNav.heading}</p>
        <ul className="footer-nav-list">
          {relatedLinks.map((link) => (
            <li key={link.href}>
              <a href={link.href}>{link.label}</a>
            </li>
          ))}
        </ul>
      </nav>
    </footer>
  );
}
