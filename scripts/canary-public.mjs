#!/usr/bin/env node

import process from "node:process";
import { pathToFileURL } from "node:url";

const DEFAULT_PATHS = [
  "/",
  "/pokemon",
  "/one-piece",
  "/watchlist",
  "/graders/psa",
  "/graders/bgs",
  "/graders/cgc",
  "/graders/sgc",
];
const PRIVATE_PATHS = [
  "/api/snapshot",
  "/latest.json",
  "/generations/canary-probe/snapshot.json",
  "/data/private/canary-probe.json",
  "/data/public/seed-snapshot.json",
];
const FORBIDDEN = [
  ["private provider token", /\b(?:g10|gemrate|snkrdunk|sneakerdunk|ebay)\b/i],
  ["private data path", /(?:data[\\/]+private|source-history\.sqlite|canonical-history\.sqlite)/i],
  ["unmasked slab marker", /(?:slab[_-]?unmasked|unmasked[_-]?slab|cert[_-]?visible|barcode[_-]?visible)/i],
  ["source map reference", /sourceMappingURL\s*=/i],
];
const GENERATION_ID_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$/;

export function resolveRenderedGeneration(response, html) {
  const headerGeneration = response.headers.get("x-cardz-generation")?.trim() ?? "";
  const attributeMatch = html.match(/\bdata-cardz-generation=(?:"([A-Za-z0-9_-]{1,128})"|'([A-Za-z0-9_-]{1,128})')/);
  const generation = headerGeneration || attributeMatch?.[1] || attributeMatch?.[2] || "";
  if (generation && !GENERATION_ID_PATTERN.test(generation)) throw new Error("page exposes an unsafe CARDZ generation ID");
  return generation;
}

function parseArgs(argv) {
  const options = {
    origin: process.env.CARDZ_CANARY_ORIGIN ?? "",
    expectedBuild: process.env.CARDZ_EXPECTED_BUILD_ID ?? "",
    expectedGeneration: process.env.CARDZ_EXPECTED_GENERATION ?? "",
    paths: DEFAULT_PATHS,
    discovery: process.env.CARDZ_CANARY_DISCOVERY ?? "public",
  };
  for (let index = 0; index < argv.length; index += 1) {
    const argument = argv[index];
    if (argument === "--origin") options.origin = argv[++index] ?? "";
    else if (argument === "--expect-build") options.expectedBuild = argv[++index] ?? "";
    else if (argument === "--expect-generation") options.expectedGeneration = argv[++index] ?? "";
    else if (argument === "--paths") options.paths = (argv[++index] ?? "").split(",").filter(Boolean);
    else if (argument === "--discovery") options.discovery = argv[++index] ?? "";
    else throw new Error(`Unknown argument: ${argument}`);
  }
  if (!options.origin) throw new Error("Canary origin is required via --origin or CARDZ_CANARY_ORIGIN.");
  const origin = new URL(options.origin);
  if (origin.protocol !== "https:" && !["localhost", "127.0.0.1"].includes(origin.hostname)) {
    throw new Error("Canary origin must use HTTPS unless it is localhost.");
  }
  options.origin = origin.origin;
  if (!["public", "private"].includes(options.discovery)) throw new Error("--discovery must be public or private.");
  return options;
}

async function readBounded(response, maximumBytes = 2 * 1024 * 1024) {
  const body = new Uint8Array(await response.arrayBuffer());
  if (body.byteLength > maximumBytes) throw new Error(`response exceeds ${maximumBytes} bytes`);
  return new TextDecoder().decode(body);
}

function assertSafeText(text, label, { allowDiscoveryPrivateRoute = false } = {}) {
  for (const [name, pattern] of FORBIDDEN) {
    const scannedText = name === "private data path" && allowDiscoveryPrivateRoute
      ? text.replaceAll("/data/private/", "")
      : text;
    if (pattern.test(scannedText)) throw new Error(`${label} contains ${name}`);
  }
}

function requireSecurityHeaders(response, pathname) {
  const contentSecurityPolicy = response.headers.get("content-security-policy") ?? "";
  for (const directive of ["default-src 'self'", "frame-ancestors 'none'", "object-src 'none'", "connect-src 'self'"]) {
    if (!contentSecurityPolicy.includes(directive)) throw new Error(`${pathname} CSP is missing ${directive}`);
  }
  if (/https?:\/\//i.test(contentSecurityPolicy)) throw new Error(`${pathname} CSP exposes an external origin`);
  if (response.headers.get("x-content-type-options") !== "nosniff") throw new Error(`${pathname} is missing nosniff`);
  if (response.headers.get("x-frame-options") !== "DENY") throw new Error(`${pathname} is missing DENY framing policy`);
  if (!response.headers.get("referrer-policy")) throw new Error(`${pathname} is missing Referrer-Policy`);
  if (response.url.startsWith("https:") && !response.headers.get("strict-transport-security")) {
    throw new Error(`${pathname} is missing HSTS`);
  }
  if (response.headers.has("sourcemap") || response.headers.has("x-sourcemap")) {
    throw new Error(`${pathname} advertises a source map`);
  }
}

async function fetchPage(options, pathname) {
  const url = new URL(pathname, options.origin);
  url.searchParams.set("lang", "en");
  url.searchParams.set("currency", "USD");
  const response = await fetch(url, {
    headers: { Accept: "text/html" },
    redirect: "follow",
    signal: AbortSignal.timeout(15_000),
  });
  if (response.status !== 200) throw new Error(`${pathname} returned ${response.status}`);
  if (!(response.headers.get("content-type") ?? "").includes("text/html")) {
    throw new Error(`${pathname} did not return HTML`);
  }
  requireSecurityHeaders(response, pathname);
  const build = response.headers.get("x-cardz-build") ?? "";
  if (!build) throw new Error(`${pathname} is missing X-CARDZ-Build`);
  if (options.expectedBuild && build !== options.expectedBuild) {
    throw new Error(`${pathname} build ${build} does not match ${options.expectedBuild}`);
  }
  const body = await readBounded(response);
  assertSafeText(body, pathname);
  const generation = resolveRenderedGeneration(response, body);
  if (options.expectedGeneration && generation !== options.expectedGeneration) {
    throw new Error(`${pathname} generation ${generation || "<missing>"} does not match ${options.expectedGeneration}`);
  }
  return { pathname, build, generation: generation || null };
}

async function assertPrivateBoundary(options, pathname) {
  const response = await fetch(new URL(pathname, options.origin), {
    headers: { Accept: "application/json" },
    redirect: "follow",
    signal: AbortSignal.timeout(15_000),
  });
  if (![401, 403, 404, 405].includes(response.status)) {
    throw new Error(`${pathname} must not be a public bulk-data surface; received ${response.status}`);
  }
  return { pathname, status: response.status };
}

async function assertDiscoveryBoundary(options) {
  const robotsResponse = await fetch(new URL("/robots.txt", options.origin), {
    headers: { Accept: "text/plain" },
    redirect: "follow",
    signal: AbortSignal.timeout(15_000),
  });
  if (robotsResponse.status !== 200) throw new Error(`/robots.txt returned ${robotsResponse.status}`);
  requireSecurityHeaders(robotsResponse, "/robots.txt");
  const robots = await readBounded(robotsResponse, 256 * 1024);
  assertSafeText(robots, "/robots.txt", { allowDiscoveryPrivateRoute: true });
  if (options.discovery === "private") {
    if (!/User-agent:\s*\*[\s\S]*Disallow:\s*\/(?:\s|$)/i.test(robots)) {
      throw new Error("canary /robots.txt must globally disallow crawling");
    }
    if (/^Sitemap:/im.test(robots)) throw new Error("canary /robots.txt must not advertise a sitemap");
    return {
      robots: { pathname: "/robots.txt", status: robotsResponse.status },
      sitemap: null,
    };
  }
  for (const directive of ["Disallow: /api/", "Disallow: /data/private/", "Disallow: /latest.json", "Disallow: /generations/"]) {
    if (!robots.includes(directive)) throw new Error(`/robots.txt is missing ${directive}`);
  }
  if (!/User-agent:\s*GPTBot[\s\S]*Disallow:\s*\//i.test(robots)) {
    throw new Error("/robots.txt must independently block the training crawler GPTBot");
  }

  const sitemapResponse = await fetch(new URL("/sitemap.xml", options.origin), {
    headers: { Accept: "application/xml,text/xml" },
    redirect: "follow",
    signal: AbortSignal.timeout(15_000),
  });
  if (sitemapResponse.status !== 200) throw new Error(`/sitemap.xml returned ${sitemapResponse.status}`);
  requireSecurityHeaders(sitemapResponse, "/sitemap.xml");
  const sitemap = await readBounded(sitemapResponse, 2 * 1024 * 1024);
  assertSafeText(sitemap, "/sitemap.xml");
  if (!/<urlset\b/i.test(sitemap)) throw new Error("/sitemap.xml does not contain a URL set");
  if (!/hreflang=["']x-default["']/i.test(sitemap)) throw new Error("/sitemap.xml is missing the x-default language alternate");
  for (const forbiddenPath of ["/api/", "/latest.json", "/generations/", "/data/private/"]) {
    if (sitemap.includes(forbiddenPath)) throw new Error(`/sitemap.xml exposes ${forbiddenPath}`);
  }
  return {
    robots: { pathname: "/robots.txt", status: robotsResponse.status },
    sitemap: { pathname: "/sitemap.xml", status: sitemapResponse.status },
  };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const pages = [];
  for (const pathname of options.paths) pages.push(await fetchPage(options, pathname));
  const discovery = await assertDiscoveryBoundary(options);
  const boundaries = [];
  for (const pathname of PRIVATE_PATHS) boundaries.push(await assertPrivateBoundary(options, pathname));
  console.log(JSON.stringify({ origin: options.origin, pages, discovery, privateBoundaries: boundaries }, null, 2));
  console.log("PASS public canary: routes, discovery policy, security headers, build/generation, leak scan, and private data boundary.");
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main().catch((error) => {
    console.error(`FAIL public canary: ${error.message}`);
    process.exitCode = 1;
  });
}
