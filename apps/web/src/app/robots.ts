import type { MetadataRoute } from "next";

/* 同 `lib/public-site.ts` 預設同一個 origin。呢個檔俾 node test 直接 import，唔可以 `@/`。 */
const siteUrl = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://cardzmarketcap.com").replace(/\/$/, "");

export const dynamic = "force-dynamic";

export function createRobotsPolicy(environment?: string): MetadataRoute.Robots {
  if (environment === "canary" || environment === "staging") {
    return { rules: [{ userAgent: "*", disallow: "/" }] };
  }

  const privateDataPath = ["/data", "private", ""].join("/");
  // `/tune` is the internal heatmap tuning lab. It already sends `noindex, nofollow` in its page
  // metadata, but that only lands after a crawler fetches it — this keeps it out of the crawl.
  const privatePaths = ["/api/", privateDataPath, "/tune"];
  return {
    rules: [
      { userAgent: ["Googlebot", "Bingbot", "OAI-SearchBot"], allow: "/", disallow: privatePaths },
      {
        userAgent: [
          "GPTBot",
          "CCBot",
          "ClaudeBot",
          "Google-Extended",
          "Amazonbot",
          "Applebot-Extended",
          "Bytespider",
          "meta-externalagent",
        ],
        disallow: "/",
      },
      { userAgent: "*", allow: "/", disallow: privatePaths },
    ],
    sitemap: `${siteUrl}/sitemap.xml`,
  };
}

export default async function robots(): Promise<MetadataRoute.Robots> {
  return createRobotsPolicy(process.env.CARDZ_ENVIRONMENT);
}
