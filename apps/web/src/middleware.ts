import { NextResponse, type NextRequest } from "next/server";
import {
  BOT_UA_PATTERN,
  COUNTRY_HEADERS,
  CURRENCY_COOKIE,
  LANG_COOKIE,
  geoRedirectSearch,
  resolveGeoDefaults,
} from "@/lib/geo-defaults";
import { aliasHostRedirectLocation } from "@/lib/public-site";

const htmlLanguages = {
  en: "en",
  "zh-TW": "zh-Hant",
  "zh-CN": "zh-Hans",
  ja: "ja",
  ko: "ko",
} as const;

/*
 * 只對真人嘅 HTML document navigation 做 geo 預設；RSC / prefetch / bot / 靜態檔一律唔郁。
 * ⚠️ Next 嘅 middleware adapter 會喺入 middleware 之前剝走 `rsc` / `next-router-prefetch` /
 * `next-router-state-tree`（FLIGHT_HEADERS，「middleware 只見 page request」），所以淨靠嗰幾個 header
 * 認唔到 RSC fetch（實測 curl -H "rsc: 1" 照 302）。真正靠得住嘅係 client router 冇剝嘅
 * `accept: text/x-component` 同瀏覽器自帶嘅 `sec-fetch-dest` / `sec-fetch-mode`；rsc 嗰幾個留住做保險。
 */
function isDocumentNavigation(request: NextRequest): boolean {
  if (request.method !== "GET") return false;
  const headers = request.headers;
  if (headers.has("rsc") || headers.has("next-router-prefetch") || headers.has("next-router-state-tree")) return false;
  if ((headers.get("accept") ?? "").includes("text/x-component")) return false;
  if (headers.get("purpose") === "prefetch") return false;
  const dest = headers.get("sec-fetch-dest");
  if (dest && dest !== "document") return false;
  const mode = headers.get("sec-fetch-mode");
  if (mode && mode !== "navigate") return false;
  if (BOT_UA_PATTERN.test(headers.get("user-agent") ?? "")) return false;
  const { pathname } = request.nextUrl;
  if (pathname === "/api" || pathname.startsWith("/api/") || pathname.startsWith("/_next/")) return false;
  /* 有副檔名當 asset（/brand/*.png、/icons/*、manifest.webmanifest、robots.txt、sitemap.xml…） */
  if (/\.[a-z0-9]+$/i.test(pathname)) return false;
  return true;
}

function countryFromHeaders(request: NextRequest): string | null {
  for (const name of COUNTRY_HEADERS) {
    const value = request.headers.get(name);
    if (value) return value;
  }
  return null;
}

export function middleware(request: NextRequest) {
  const location = aliasHostRedirectLocation(
    request.headers.get("host"),
    request.nextUrl.pathname,
    request.nextUrl.search,
  );
  if (location) return NextResponse.redirect(location, 301);

  /*
   * GEO 預設（owner 2026-08-16）：URL 冇 lang / currency 嗰陣，cookie 有就跟 cookie，
   * 冇就跟 IP 國家（JP→ja/JPY、KR→ko/KRW、TW→zh-TW/TWD、HK/MO→zh-TW/HKD、CN→zh-CN/CNY、GB→en/GBP），
   * resolve 出嚟唔係 en / USD 就 302 去同一條 path 加 param（其他 param 保留）。
   * redirect 後 URL 一定帶住 param，所以第二次唔會再 match，唔會迴圈。
   *
   * ⚠️ 呢招只喺 origin 見到每一個 HTML request 先有效 —— 而家 Cloudflare 對 HTML 係 DYNAMIC（唔 cache），
   * 所以 OK。日後如果開 page cache，cache key 一定要 vary by country（cf-ipcountry）+ cookie，
   * 否則第一個 JP 人嘅 302 會 cache 咗畀全世界。302 本身落 no-store + Vary: Cookie 頂住第一層。
   */
  if (isDocumentNavigation(request)) {
    const defaults = resolveGeoDefaults(countryFromHeaders(request), {
      lang: request.cookies.get(LANG_COOKIE)?.value,
      currency: request.cookies.get(CURRENCY_COOKIE)?.value,
    });
    const nextSearch = geoRedirectSearch(request.nextUrl.searchParams, defaults);
    if (nextSearch) {
      const url = request.nextUrl.clone();
      url.search = nextSearch.toString();
      const response = NextResponse.redirect(url, 302);
      response.headers.set("Vary", "Cookie");
      response.headers.set("Cache-Control", "private, no-store");
      return response;
    }
  }

  const requested = request.nextUrl.searchParams.get("lang") as keyof typeof htmlLanguages | null;
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-cardz-html-lang", requested && htmlLanguages[requested] ? htmlLanguages[requested] : "en");
  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  /* S / www 跟 host 301 去 cardzmarketcap.com；/api/* 刻意唔入 matcher，health 保持 200。 */
  matcher: ["/((?!api|_next/static|_next/image|market-assets|favicon.ico).*)"],
};
