import type { Metadata } from "next";
import { copy } from "./i18n";
import { normaliseLocale } from "./format";
import { plainDescription } from "./plain-text";
import { PUBLIC_SITE_URL } from "./public-site";
import type { Locale } from "./types";

export type PageSearchParams = Promise<Record<string, string | string[] | undefined>>;

export async function localeFromSearchParams(searchParams: PageSearchParams): Promise<Locale> {
  const value = (await searchParams).lang;
  return normaliseLocale(Array.isArray(value) ? value[0] : value);
}

/* canonical / hreflang / og:url 三樣要係同一條 path，所以 export 出去畀其他 route 行同一份。 */
export function localizedPath(path: string, locale: Locale): string {
  if (locale === "en") return path;
  return `${path}${path.includes("?") ? "&" : "?"}lang=${locale}`;
}

/*
 * `twitter:card` 聲明咗 `summary_large_image`，呢種卡片型式強制要圖，冇圖就出一張空白大卡。
 * Next 嘅 `opengraph-image` file convention 喺呢個 app 靠唔住：每版都有自己嘅
 * `generateMetadata` 回傳 `openGraph`，會冚走由上層繼承落嚟嘅 images（實測 `/` 有圖，
 * `/pokemon` `/watchlist` `/card/*` 全部 MISSING）。所以 og:image 一律喺呢度
 * 落，呢個係全站唯一嘅 metadata 出口，冇得漏。
 */
const DEFAULT_OG_IMAGE = "/brand/og-light.png";

/*
 * OG spec 個 locale 係底線式（`zh_TW`），唔係 BCP 47（`zh-TW`）。以前呢度直接遞 app
 * locale 原字串出街，即係五個 locale 入面有四個出咗一個 unfurler 唔認得嘅值。
 * 冇 crash、冇警告 —— 錯法係靜默嘅，所以要用一張明表釘死。
 */
const OG_LOCALE: Record<Locale, string> = {
  en: "en_US",
  "zh-TW": "zh_TW",
  "zh-CN": "zh_CN",
  ja: "ja_JP",
  ko: "ko_KR",
};

export function marketMetadata(
  locale: Locale,
  title: string,
  description: string,
  path = "/",
  image: string = DEFAULT_OG_IMAGE,
  imageAlt = "CardZ Marketcap",
  imageType: "image/png" | "image/jpeg" = "image/png",
): Metadata {
  /*
   * `secureUrl` / `type` 唔係裝飾：部分 unfurler（尤其 Slack／舊 FB scraper）見唔到
   * `og:image:type` 就要自己下載成張圖 sniff MIME 先肯 render 大卡。
   * ⚠️ `type` 一定要同真 bytes 一致。2026-08-19：card 頁嘅 OG route 為咗壓落 WhatsApp
   * 600KB 閘已經轉咗出 JPEG（見 `api/og/card/[id]/route.tsx` 個 `respond()`），
   * 所以呢粒係參數，唔再係寫死 PNG —— 靜態頁仍然係 `/brand/og-light.png`。
   * `scripts/test-fe-og-unfurl.mjs` 會攞真 bytes 對返呢粒。
   */
  /*
   * ⚠️ 兩粒都要**自己**變絕對 URL。Next 只會攞 `metadataBase` 解 `url`，
   * `secureUrl` 佢原封不動照抄出街 —— 實測 2026-08-19 出街嗰版：
   *   og:image            = https://cardzmarketcap.com/api/og/card/…  ✅
   *   og:image:secure_url = /api/og/card/…                            ❌ 相對路徑
   * `og:image:secure_url` 按 spec 一定要係絕對 https URL。相對值唔會令頁面炸，
   * 但 scraper 解唔到就當冇 secure_url，部分（HTTPS-only 嗰批）直接跌返細卡。
   */
  const imageUrl = new URL(image, PUBLIC_SITE_URL).toString();
  const images = [{ url: imageUrl, secureUrl: imageUrl, type: imageType, width: 1200, height: 630, alt: imageAlt }];
  /*
   * description 喺呢度正規化一次，唔喺叫方度做。
   * 傳入嚟嘅可以係 hero 文案（本身已經係純文字，行完一樣），亦可以係 card 頁嗰篇
   * markdown 小故事（`/card/[id]/page.tsx:50` 直接遞 `card.story[locale]`）。三個
   * 出口（`description`、`og:description`、`twitter:description`）用同一個值，所以
   * 得呢一句就冚到晒；喺叫方逐個做就實有日漏一個。
   */
  const summary = plainDescription(description);
  return {
    title,
    description: summary,
    alternates: {
      canonical: localizedPath(path, locale),
      languages: {
        en: localizedPath(path, "en"),
        "zh-Hant": localizedPath(path, "zh-TW"),
        "zh-Hans": localizedPath(path, "zh-CN"),
        ja: localizedPath(path, "ja"),
        ko: localizedPath(path, "ko"),
        "x-default": localizedPath(path, "en"),
      },
    },
    /*
     * og:url 要絕對 URL（GEO，owner 2026-08-16）。以前成站冇出過呢粒 meta，分享／
     * 爬蟲就要自己估邊條先係正本；而家同 canonical 行同一個 localizedPath，兩者
     * 永遠對得住（en 裸 path，其餘帶 ?lang=）。
     */
    openGraph: {
      title,
      description: summary,
      siteName: "CardZ Marketcap",
      type: "website",
      locale: OG_LOCALE[locale],
      url: new URL(localizedPath(path, locale), PUBLIC_SITE_URL).toString(),
      images,
    },
    twitter: { card: "summary_large_image", title, description: summary, images },
  };
}

export function defaultMarketMetadata(locale: Locale): Metadata {
  const t = copy[locale];
  /*
   * 首頁要自己帶 `| CardZ Marketcap`：Next 嘅 title.template 只套用落**子** segment，
   * 而 app/page.tsx 同定義個 template 嘅 app/layout.tsx 係同一個 segment，所以套唔到。
   * 其餘頁面唔好照抄呢句，否則會出兩次品牌名。—— verify pass 2026-08-16
   */
  return marketMetadata(locale, `${t.seo.home.title} | CardZ Marketcap`, t.seo.home.description, "/");
}
