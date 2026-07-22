"use client";

import { useEffect } from "react";
import { useMarketSettings } from "@/lib/use-market-settings";

export function DocumentLanguage() {
  const { locale } = useMarketSettings();
  useEffect(() => {
    document.documentElement.lang = locale === "zh-TW" ? "zh-Hant" : locale === "zh-CN" ? "zh-Hans" : locale;
  }, [locale]);
  return null;
}
