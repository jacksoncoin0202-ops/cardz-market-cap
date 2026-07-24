"use client";

import { useEffect } from "react";
import { useMarketSettings } from "@/lib/use-market-settings";

const themeScript = `(function(){try{var s=localStorage.getItem("cardz-theme");var t=s==="dark"||s==="light"?s:(window.matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light");document.documentElement.dataset.theme=t}catch(e){document.documentElement.dataset.theme="light"}})();`;

const themeTransitionScript = `(function(){requestAnimationFrame(function(){requestAnimationFrame(function(){document.documentElement.classList.add("theme-transitions")})})})();`;

export function ThemeScript() {
  return (
    <>
      <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      <script dangerouslySetInnerHTML={{ __html: themeTransitionScript }} />
    </>
  );
}

export function DocumentLanguage() {
  const { locale } = useMarketSettings();
  useEffect(() => {
    document.documentElement.lang = locale === "zh-TW" ? "zh-Hant" : locale === "zh-CN" ? "zh-Hans" : locale;
  }, [locale]);
  return null;
}
