import { NextResponse, type NextRequest } from "next/server";
import { aliasHostRedirectLocation } from "@/lib/public-site";

const htmlLanguages = {
  en: "en",
  "zh-TW": "zh-Hant",
  "zh-CN": "zh-Hans",
  ja: "ja",
  ko: "ko",
} as const;

export function middleware(request: NextRequest) {
  const location = aliasHostRedirectLocation(
    request.headers.get("host"),
    request.nextUrl.pathname,
    request.nextUrl.search,
  );
  if (location) return NextResponse.redirect(location, 301);

  const requested = request.nextUrl.searchParams.get("lang") as keyof typeof htmlLanguages | null;
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-cardz-html-lang", requested && htmlLanguages[requested] ? htmlLanguages[requested] : "en");
  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  /* robots / sitemap 跟 host 301；/api/* 刻意唔入 matcher，ALB 同日鏈 health 保持 200。 */
  matcher: ["/((?!api|_next/static|_next/image|market-assets|favicon.ico).*)"],
};
