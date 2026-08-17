import { currencies, type Currency, type Locale } from "./types";

/*
 * 貨幣選單嘅「樣」：符號（= logo）、地區分組、本地化名。
 * 純資料 + 一個函數，冇 "use client" —— server 同 client 都 import 得。
 *
 * 點解係符號唔係國旗：一隻貨幣唔等於一個國家（EUR 冇國旗、USD 唔止美國用），
 * 而且 emoji 國旗喺 Windows 根本 render 唔到（出兩個字母方格）。符號係貨幣自己嘅身份。
 */

/* 符號 = 「logo」。同一隻貨幣喺 trigger 同選項度都用呢個。 */
export const currencySymbol: Record<Currency, string> = {
  USD: "$",
  HKD: "HK$",
  TWD: "NT$",
  JPY: "¥",
  KRW: "₩",
  CNY: "CN¥",
  SGD: "S$",
  MYR: "RM",
  THB: "฿",
  PHP: "₱",
  IDR: "Rp",
  VND: "₫",
  INR: "₹",
  AUD: "A$",
  NZD: "NZ$",
  EUR: "€",
  GBP: "£",
  CHF: "CHF",
  SEK: "kr",
  NOK: "kr",
  DKK: "kr",
  PLN: "zł",
  CZK: "Kč",
  CAD: "C$",
  MXN: "MX$",
  BRL: "R$",
  AED: "د.إ",
  SAR: "﷼",
  ILS: "₪",
  TRY: "₺",
  ZAR: "R",
};

export type CurrencyRegion = "asia" | "americas" | "europe" | "mea";

export const currencyRegion: Record<Currency, CurrencyRegion> = {
  USD: "americas",
  CAD: "americas",
  MXN: "americas",
  BRL: "americas",
  HKD: "asia",
  TWD: "asia",
  JPY: "asia",
  KRW: "asia",
  CNY: "asia",
  SGD: "asia",
  MYR: "asia",
  THB: "asia",
  PHP: "asia",
  IDR: "asia",
  VND: "asia",
  INR: "asia",
  AUD: "asia",
  NZD: "asia",
  EUR: "europe",
  GBP: "europe",
  CHF: "europe",
  SEK: "europe",
  NOK: "europe",
  DKK: "europe",
  PLN: "europe",
  CZK: "europe",
  AED: "mea",
  SAR: "mea",
  ILS: "mea",
  TRY: "mea",
  ZAR: "mea",
};

/* 選單分組次序：亞太行先（站嘅讀者主要喺 HK / TW / JP / KR），之後美洲、歐洲、中東非洲。 */
export const currencyRegionOrder: readonly CurrencyRegion[] = ["asia", "americas", "europe", "mea"];

/*
 * 選單次序：USD 釘最頂（owner 2026-08-17「usd默認最頂」—— 佢係 base 兼預設，唔應該埋喺美洲組第 16 位），
 * 之後按地區：地區之間跟 `currencyRegionOrder`，地區入面保持 `currencies` 嘅正典次序。
 * 唔喺 `list` 入面嘅貨幣唔會憑空多咗出嚟 —— 呢個函數只係重排，唔加唔減。
 */
export function currencyMenuOrder(list: readonly Currency[]): Currency[] {
  const wanted = new Set(list);
  const ordered: Currency[] = wanted.has("USD") ? ["USD"] : [];
  for (const region of currencyRegionOrder) {
    for (const code of currencies) {
      if (code !== "USD" && wanted.has(code) && currencyRegion[code] === region) ordered.push(code);
    }
  }
  return ordered;
}

/* 選單分組 heading 用：USD 釘咗喺頂，唔屬任何組（null = 唔出 heading）；其餘跟 `currencyRegion`。 */
export function currencyMenuGroup(code: Currency): CurrencyRegion | null {
  return code === "USD" ? null : currencyRegion[code];
}

const intlTag: Record<Locale, string> = {
  en: "en-US",
  "zh-TW": "zh-Hant-TW",
  "zh-CN": "zh-Hans-CN",
  ja: "ja-JP",
  ko: "ko-KR",
};

/*
 * 本地化貨幣名由 ICU（`Intl.DisplayNames`）出，唔手寫 31 × 5 張表 —— 手寫表一定會過時，
 * 而且五個語系全部要人翻，冇人守得住。
 *
 * client-only 用法：選單只喺用戶撳開之後先 render，所以 server 同 client render 唔會對唔上，
 * 冇 hydration mismatch 風險。舊 runtime 冇 `Intl.DisplayNames`（或者某隻貨幣冇資料）
 * → `fallback: "none"` 回 undefined，呢度回 `null`，呼叫方唔畫個空 span。
 */
const displayNamesCache = new Map<Locale, Intl.DisplayNames | null>();

function displayNamesFor(locale: Locale): Intl.DisplayNames | null {
  if (displayNamesCache.has(locale)) return displayNamesCache.get(locale) ?? null;
  let instance: Intl.DisplayNames | null = null;
  try {
    instance = new Intl.DisplayNames([intlTag[locale]], { type: "currency", fallback: "none" });
  } catch {
    instance = null;
  }
  displayNamesCache.set(locale, instance);
  return instance;
}

export function currencyDisplayName(code: Currency, locale: Locale): string | null {
  const names = displayNamesFor(locale);
  if (!names) return null;
  try {
    const name = names.of(code);
    return typeof name === "string" && name.trim() && name !== code ? name : null;
  } catch {
    return null;
  }
}
