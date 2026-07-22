import { getCloudflareContext } from "@opennextjs/cloudflare";
import type { MetadataRoute } from "next";

const siteUrl = process.env.NEXT_PUBLIC_SITE_URL ?? "https://cardz-beta.jacksoncoin0202.workers.dev";

export const dynamic = "force-dynamic";

export function createRobotsPolicy(environment?: string): MetadataRoute.Robots {
  if (environment === "canary" || environment === "staging") {
    return { rules: [{ userAgent: "*", disallow: "/" }] };
  }

  const privateDataPath = ["/data", "private", ""].join("/");
  const privatePaths = ["/api/", privateDataPath, "/latest.json", "/generations/"];
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
  try {
    const context = await getCloudflareContext({ async: true });
    const environment = context.env as unknown as { CARDZ_ENVIRONMENT?: string };
    return environment.CARDZ_ENVIRONMENT ?? process.env.CARDZ_ENVIRONMENT;
  } catch {
    return process.env.CARDZ_ENVIRONMENT;
  }
}

export default async function robots(): Promise<MetadataRoute.Robots> {
  return createRobotsPolicy(await runtimeEnvironment());
}
