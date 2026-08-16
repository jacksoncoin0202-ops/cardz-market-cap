"use client";

import { useEffect } from "react";
import { copy } from "@/lib/i18n";
import { useMarketSettings } from "@/lib/use-market-settings";

/*
 * Paint 前一次過定好三件 client-only 事實，全部落 <html>：
 *  - data-theme：localStorage `cardz-theme`，冇就跟 OS。server 唔出 data-theme（避免 hydration mismatch）。
 *  - data-updown：localStorage `cardz-updown`（red-up / green-up）；冇就一律 green-up
 *    （owner 2026-08-16：全部 locale 默認綠升紅跌，唔跟語言自動轉，只有設定掣先反轉）。
 *  - <meta name="theme-color">：自己 prepend 一粒冇 media 嘅 meta 落 head 頭（tree order 排第一，
 *    瀏覽器取第一粒 match 嘅，所以會蓋過 viewport export 出嗰兩粒 media meta），之後用
 *    MutationObserver 睇住 data-theme 變就同步 —— toggle 嗰邊（use-market-settings）唔使識呢度。
 * 唔加 `theme-transitions` class：由 setTheme 加 260ms 就除，唔再係永久 class（A7）。
 */
const themeScript = `(function(){var d=document.documentElement;var t="light";try{var s=localStorage.getItem("cardz-theme");t=s==="dark"||s==="light"?s:(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light")}catch(e){}d.dataset.theme=t;try{var u=localStorage.getItem("cardz-updown");d.dataset.updown=u==="red-up"?"red-up":"green-up"}catch(e){d.dataset.updown="green-up"}function sync(){try{var c=d.dataset.theme==="dark"?"#101010":"#f7f7f5";var meta=document.querySelector('meta[data-cardz-theme-color]');if(!meta){meta=document.createElement("meta");meta.name="theme-color";meta.setAttribute("data-cardz-theme-color","");document.head.insertBefore(meta,document.head.firstChild)}if(meta.content!==c)meta.content=c}catch(e){}}sync();new MutationObserver(sync).observe(d,{attributes:true,attributeFilter:["data-theme"]})})();`;

const langScript = `(function(){try{var q=new URLSearchParams(location.search).get("lang");document.documentElement.lang=q==="zh-TW"?"zh-Hant":q==="zh-CN"?"zh-Hans":q==="ja"||q==="ko"||q==="en"?q:"en"}catch(e){document.documentElement.lang="en"}})();`;

export function ThemeScript() {
  return <script dangerouslySetInnerHTML={{ __html: themeScript }} />;
}

export function LangScript() {
  return <script dangerouslySetInnerHTML={{ __html: langScript }} />;
}

/* body 第一個 child：鍵盤 / 讀屏跳過 header 直落 <main id="main">。文案跟 client locale（Suspense fallback 出英文）。 */
export function SkipLink() {
  const { locale } = useMarketSettings();
  return <a className="skip-link" href="#main">{copy[locale].skipToContent}</a>;
}

export function DocumentLanguage() {
  const { locale } = useMarketSettings();
  useEffect(() => {
    document.documentElement.lang = locale === "zh-TW" ? "zh-Hant" : locale === "zh-CN" ? "zh-Hans" : locale;
  }, [locale]);
  return null;
}
