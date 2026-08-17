import type { Locale, MarketCardView } from "./types";

/*
 * 卡名顯示規則（owner 2026-08-16）：轉咗語言就全站用當地語言名 —— 熱力圖 hover 預覽、
 * bottom sheet、tile aria、排行榜、卡頁 H1／<title>／JSON-LD 全部同一個 helper。
 *
 * 來源：`card.name[locale]` 由 server-snapshot.ts applyCardNameOverlays 由
 * data/editorial/card-names-by-id.json 畫上去（Pokémon 官方標準譯名，例如「皮卡丘」，唔係港式）。
 * 冇譯名／en locale → 退返 PSA/GemRate 英文 officialName。
 */
export function displayCardName(
  card: Pick<MarketCardView, "officialName" | "name">,
  locale: Locale,
  fallback = "",
): string {
  if (locale === "en") return card.officialName || card.name?.en || fallback;
  return card.name?.[locale] || card.officialName || card.name?.en || fallback;
}

/*
 * <html lang> / <span lang> 用嘅 BCP 47：zh-TW → zh-Hant、zh-CN → zh-Hans，其餘照 locale。
 * 同 document-language.tsx（LangScript + DocumentLanguage）一致；globals.css 嘅 `[lang]:lang()`
 * 字體 stack 同 CJK token 覆蓋只認呢五個值 —— 唔准喺 JSX 硬寫 lang="zh-TW"。
 */
export type HtmlLang = "en" | "ja" | "ko" | "zh-Hant" | "zh-Hans";
export function htmlLang(locale: Locale): HtmlLang {
  return locale === "zh-TW" ? "zh-Hant" : locale === "zh-CN" ? "zh-Hans" : locale;
}

/*
 * displayCardName 顯示出嚟嗰串字係咩語言（同 displayCardName 同一分支）：譯名存在 → 頁面語言；
 * 跌落 officialName / name.en → "en"。**唔用 card.cardLanguage**——嗰個係實體卡嘅印刷語言，
 * 唔係顯示緊嘅名嘅語言。用法：`<strong lang={cardNameLangAttr(card, locale)}>`——
 * 同頁面語言一樣就唔出屬性（undefined），唔同（CJK 頁跌落英文名）先出 lang="en"，
 * 令讀屏唔會用 CJK 口音讀英文名。
 * ⚠️ CSS 方面 lang="en" **唔會自己**解除 CJK 斷行：ko 嘅 keep-all 係繼承落嚟、CJK line-break: strict
 * 係按 tag/class 直接命中（`:is(:lang(ja),…) :is(h1, .mobile-card-name, …)`），兩條都唔會因為
 * 元素自己標咗 lang="en" 而唔 match。真正嘅解除係 globals.css 嗰條
 * `:is(:lang(ja),:lang(ko),:lang(zh-Hant),:lang(zh-Hans)) [lang="en"]` 明寫規則 —— 改嗰條之前睇清楚。
 */
export function displayCardNameLang(
  card: Pick<MarketCardView, "officialName" | "name">,
  locale: Locale,
): HtmlLang {
  if (locale === "en") return "en";
  return card.name?.[locale] ? htmlLang(locale) : "en";
}

export function cardNameLangAttr(
  card: Pick<MarketCardView, "officialName" | "name">,
  locale: Locale,
): HtmlLang | undefined {
  const lang = displayCardNameLang(card, locale);
  return lang === htmlLang(locale) ? undefined : lang;
}
