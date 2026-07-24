import { NextResponse, type NextRequest } from "next/server";

const htmlLanguages = {
  en: "en",
  "zh-TW": "zh-Hant",
  "zh-CN": "zh-Hans",
  ja: "ja",
  ko: "ko",
} as const;

export function middleware(request: NextRequest) {
  const requested = request.nextUrl.searchParams.get("lang") as keyof typeof htmlLanguages | null;
  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-cardz-html-lang", requested && htmlLanguages[requested] ? htmlLanguages[requested] : "en");
  return NextResponse.next({ request: { headers: requestHeaders } });
}

export const config = {
  matcher: ["/((?!api|_next/static|_next/image|market-assets|favicon.ico|sitemap.xml|robots.txt).*)"],
};
