export const PUBLIC_SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL ?? "https://cardsmarketcap.com").replace(
  /\/$/,
  "",
);

export const PUBLIC_CANONICAL_HOST = new URL(PUBLIC_SITE_URL).hostname;

export const PUBLIC_SITE_ALIAS_HOSTS = [
  "cardzmarketcap.com",
  "www.cardzmarketcap.com",
  "app.cardzmarketcap.com",
  "www.cardsmarketcap.com",
  "app.cardsmarketcap.com",
] as const;

export const PUBLIC_SITE_SAME_AS = [
  "https://cardzmarketcap.com",
  "https://www.cardzmarketcap.com",
  "https://app.cardzmarketcap.com",
] as const;

export function aliasHostRedirectLocation(
  hostHeader: string | null | undefined,
  pathname: string,
  search = "",
): string | null {
  const host = (hostHeader ?? "").split(":")[0]?.toLowerCase() ?? "";
  if (!host || host === PUBLIC_CANONICAL_HOST) return null;
  if (!(PUBLIC_SITE_ALIAS_HOSTS as readonly string[]).includes(host)) return null;
  if (pathname === "/api" || pathname.startsWith("/api/")) return null;
  const path = pathname.startsWith("/") ? pathname : `/${pathname}`;
  return `${PUBLIC_SITE_URL}${path}${search}`;
}

export function siteOrganization(url: string) {
  const canonical = url.replace(/\/$/, "");
  return {
    "@type": "Organization" as const,
    name: "Cards Marketcap",
    url,
    sameAs: PUBLIC_SITE_SAME_AS.filter((alias) => alias !== canonical),
  };
}
