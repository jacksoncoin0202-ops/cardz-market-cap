import {
  catalogMatchKind,
  searchCatalogPage,
  type CatalogMatchKind,
  type CatalogSearchOptions,
} from "./catalog-search";
import { PUBLIC_SITE_URL } from "./public-site";
import { locales, type CatalogEntry, type Locale, type MarketMetric } from "./types";

export const SEARCH_LIMIT_DEFAULT = 8;
export const SEARCH_LIMIT_MAX = 50;
export const RESOLVE_CANDIDATE_CAP = 5;

const STRONG_MATCH: ReadonlySet<CatalogMatchKind> = new Set([
  "number-exact",
  "number-prefix",
  "name-exact",
  "name-prefix",
  "official-prefix",
]);

export interface PublicSearchQuery {
  q: string;
  locale: Locale;
  kind?: CatalogEntry["kind"];
  tcg?: string;
  limit: number;
}

export interface PublicSearchError {
  error: string;
}

export interface PublicSearchHit {
  id: string;
  kind: CatalogEntry["kind"];
  match: CatalogMatchKind | null;
  url: string;
  href: string;
  officialName: string | null;
  name: CatalogEntry["name"];
  collectorNumber: string;
  setName: CatalogEntry["setName"];
  setCode: string | null;
  tcg: string;
  cardLanguage: CatalogEntry["cardLanguage"];
  marketRank: number;
  image: { url: string; alt: string | null };
  pricePsa10?: MarketMetric<number>;
  populationPsa10?: MarketMetric<number>;
  marketCap?: MarketMetric<number>;
}

export interface PublicSearchMeta {
  generation: { id: string };
  generatedAt: string;
  effectiveAt: string;
}

export const PUBLIC_API_CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Cache-Control": "public, s-maxage=60, stale-while-revalidate=60",
  "X-Robots-Tag": "all",
} as const;

export function attributionLine(effectiveAt: string, site = PUBLIC_SITE_URL): string {
  return `CardZ Marketcap, ${site.replace(/\/$/, "")}, data as of ${effectiveAt.slice(0, 10)}`;
}

export function absolutePublicUrl(path: string, site = PUBLIC_SITE_URL): string {
  if (/^https?:\/\//i.test(path)) return path;
  const origin = site.replace(/\/$/, "");
  return `${origin}${path.startsWith("/") ? path : `/${path}`}`;
}

function parsePositiveInt(raw: string): number | null {
  if (!/^[1-9]\d*$/.test(raw)) return null;
  const value = Number.parseInt(raw, 10);
  return Number.isSafeInteger(value) ? value : null;
}

function parseLocale(raw: string | null): Locale | PublicSearchError {
  if (raw === null || raw === "") return "en";
  const hit = locales.find((locale) => locale.toLowerCase() === raw.toLowerCase());
  if (!hit) return { error: `Unknown lang. Use one of: ${locales.join(", ")}` };
  return hit;
}

function parseKind(raw: string | null): CatalogEntry["kind"] | undefined | PublicSearchError {
  if (raw === null || raw === "") return undefined;
  if (raw === "card" || raw === "box") return raw;
  return { error: "Unknown kind. Use one of: card, box" };
}

function parseTcg(raw: string | null): string | undefined | PublicSearchError {
  if (raw === null || raw === "") return undefined;
  if (raw === "pokemon") return "Pokémon";
  if (raw === "one-piece") return "One Piece";
  return { error: "Unknown tcg. Use one of: pokemon, one-piece" };
}

function isError(value: unknown): value is PublicSearchError {
  return Boolean(value && typeof value === "object" && "error" in value);
}

export function parsePublicSearchParams(
  searchParams: URLSearchParams,
  options?: { defaultLimit?: number },
): PublicSearchQuery | PublicSearchError {
  const q = (searchParams.get("q") ?? "").trim();
  if (!q) return { error: "q is required" };
  const locale = parseLocale(searchParams.get("lang"));
  if (isError(locale)) return locale;
  const kind = parseKind(searchParams.get("kind"));
  if (isError(kind)) return kind;
  const tcg = parseTcg(searchParams.get("tcg"));
  if (isError(tcg)) return tcg;
  const rawLimit = searchParams.get("limit");
  const defaultLimit = options?.defaultLimit ?? SEARCH_LIMIT_DEFAULT;
  let limit = defaultLimit;
  if (rawLimit !== null) {
    const parsed = parsePositiveInt(rawLimit);
    if (parsed === null) return { error: "limit must be a positive integer" };
    limit = parsed;
  }
  if (limit > SEARCH_LIMIT_MAX) limit = SEARCH_LIMIT_MAX;
  return { q, locale, kind, tcg, limit };
}

export function publicSearchOptions(query: PublicSearchQuery): CatalogSearchOptions {
  return {
    kind: query.kind,
    tcg: query.tcg,
    limit: query.limit,
  };
}

export function toPublicSearchHit(entry: CatalogEntry, query: string, locale: Locale): PublicSearchHit {
  const imageUrl = entry.image.variants?.["200"] || entry.image.url;
  const hit: PublicSearchHit = {
    id: entry.id,
    kind: entry.kind,
    match: catalogMatchKind(entry, query, locale),
    url: absolutePublicUrl(entry.href),
    href: entry.href,
    officialName: entry.officialName,
    name: entry.name,
    collectorNumber: entry.collectorNumber,
    setName: entry.setName,
    setCode: entry.setCode,
    tcg: entry.tcg,
    cardLanguage: entry.cardLanguage,
    marketRank: entry.marketRank,
    image: { url: absolutePublicUrl(imageUrl), alt: entry.image.alt },
  };
  if (entry.pricePsa10) hit.pricePsa10 = entry.pricePsa10;
  if (entry.populationPsa10) hit.populationPsa10 = entry.populationPsa10;
  if (entry.marketCap) hit.marketCap = entry.marketCap;
  return hit;
}

export function runPublicSearch(
  entries: readonly CatalogEntry[],
  query: PublicSearchQuery,
): { hits: CatalogEntry[]; total: number } {
  return searchCatalogPage(entries, query.q, query.locale, publicSearchOptions(query));
}

function pickResolved(hits: readonly CatalogEntry[], query: PublicSearchQuery): {
  resolved: CatalogEntry | null;
  ambiguous: boolean;
} {
  if (hits.length === 0) return { resolved: null, ambiguous: false };
  if (hits.length === 1) return { resolved: hits[0] ?? null, ambiguous: false };
  const top = hits[0];
  const second = hits[1];
  if (!top || !second) return { resolved: top ?? null, ambiguous: false };
  const topKind = catalogMatchKind(top, query.q, query.locale);
  const secondKind = catalogMatchKind(second, query.q, query.locale);
  if (!topKind) return { resolved: null, ambiguous: false };
  if (
    (topKind === "number-exact" || topKind === "name-exact")
    && secondKind !== topKind
  ) {
    return { resolved: top, ambiguous: false };
  }
  if (STRONG_MATCH.has(topKind) && secondKind === "contains") {
    return { resolved: top, ambiguous: false };
  }
  return { resolved: null, ambiguous: true };
}

export function runPublicResolve(
  entries: readonly CatalogEntry[],
  query: PublicSearchQuery,
): { resolved: CatalogEntry | null; ambiguous: boolean; hits: CatalogEntry[]; total: number } {
  const page = searchCatalogPage(entries, query.q, query.locale, {
    kind: query.kind,
    tcg: query.tcg,
  });
  const decision = pickResolved(page.hits, query);
  const cap = decision.ambiguous ? RESOLVE_CANDIDATE_CAP : Math.min(query.limit, RESOLVE_CANDIDATE_CAP);
  return {
    resolved: decision.resolved,
    ambiguous: decision.ambiguous,
    hits: page.hits.slice(0, Math.max(cap, decision.resolved ? 1 : 0)),
    total: page.total,
  };
}

export function publicSearchBody(
  meta: PublicSearchMeta,
  query: PublicSearchQuery,
  page: { hits: CatalogEntry[]; total: number },
): Record<string, unknown> {
  return {
    ...meta,
    query: query.q,
    lang: query.locale,
    kind: query.kind ?? "all",
    tcg: query.tcg ?? "all",
    total: page.total,
    count: page.hits.length,
    citation: attributionLine(meta.effectiveAt),
    hits: page.hits.map((entry) => toPublicSearchHit(entry, query.q, query.locale)),
  };
}

export function publicResolveBody(
  meta: PublicSearchMeta,
  query: PublicSearchQuery,
  result: { resolved: CatalogEntry | null; ambiguous: boolean; hits: CatalogEntry[]; total: number },
): Record<string, unknown> {
  return {
    ...meta,
    query: query.q,
    lang: query.locale,
    kind: query.kind ?? "all",
    tcg: query.tcg ?? "all",
    total: result.total,
    ambiguous: result.ambiguous,
    citation: attributionLine(meta.effectiveAt),
    resolved: result.resolved ? toPublicSearchHit(result.resolved, query.q, query.locale) : null,
    hits: result.hits.map((entry) => toPublicSearchHit(entry, query.q, query.locale)),
  };
}
