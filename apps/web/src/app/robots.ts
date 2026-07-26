import type { MetadataRoute } from "next";
import { cloudflareEnv } from "@/lib/cloudflare-env";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "https://cardzmarketcap.com";

export const dynamic = "force-dynamic";

export function createRobotsPolicy(environment?: string): MetadataRoute.Robots {
  if (environment === "canary" || environment === "staging") {
    return { rules: [{ userAgent: "*", disallow: "/" }] };
  }

  const privateDataPath = ["/data", "private", ""].join("/");
  // `/tune` is the internal heatmap tuning lab. It already sends `noindex, nofollow` in its page
  // metadata, but that only lands after a crawler fetches it — this keeps it out of the crawl.
  const privatePaths = ["/api/", privateDataPath, "/latest.json", "/generations/", "/tune"];
  return {
    rules: [
      { userAgent: ["Googlebot", "Bingbot", "OAI-SearchBot"], allow: "/", disallow: privatePaths },
      { userAgent: ["GPTBot", "CCBot"], disallow: "/" },
      { userAgent: "*", allow: "/", disallow: privatePaths },
    ],
    sitemap: `${siteUrl}/sitemap.xml`,
  };
}

async function runtimeEnvironment(): Promise<string | undefined> {
  const environment = await cloudflareEnv<{ CARDZ_ENVIRONMENT?: string }>();
  return environment?.CARDZ_ENVIRONMENT ?? process.env.CARDZ_ENVIRONMENT;
}

export default async function robots(): Promise<MetadataRoute.Robots> {
  return createRobotsPolicy(await runtimeEnvironment());
}
