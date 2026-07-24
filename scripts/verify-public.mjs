#!/usr/bin/env node

import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const allowDemo = process.argv.includes("--allow-demo") || process.env.CARDZ_ALLOW_DEMO === "1";
const requireBuild = process.argv.includes("--require-build") || process.env.CARDZ_REQUIRE_BUILD === "1";
const failures = [];
const warnings = [];
const passes = [];

const SNAPSHOT_PATH = path.join(root, "data", "public", "seed-snapshot.json");
const LOCALES = ["en", "zhTW", "zhCN", "ja"];
const CURRENCIES = ["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"];
const WINDOWS = ["1d", "7d", "30d"];
const GRADERS = ["PSA", "BGS", "CGC", "SGC", "TAG"];
const STATUSES = new Set(["ready", "accumulating", "stale", "unavailable"]);
const COVERAGES = new Set(["partial", "stale", "unavailable"]);
const TEXT_EXTENSIONS = new Set([
  "", ".css", ".example", ".html", ".js", ".json", ".jsonc", ".jsx", ".map", ".md", ".mjs",
  ".ps1", ".py", ".sql", ".svg", ".toml", ".ts", ".tsx", ".txt", ".xml", ".yaml", ".yml",
]);

const FORBIDDEN_PUBLIC_PATTERNS = [
  ["private data path", /(?:data[\\/]+private|source-history\.sqlite|canonical-history\.sqlite)/i],
  ["provider-native value", /\bg10(?:[-_][a-z0-9_-]+)?\b/i],
  ["private provider name", /\b(?:grade10|gemrate|snkrdunk|sneakerdunk|ebay)\b/i],
  ["private provider host", /\b(?:grade10|gemrate|psacard|snkrdunk|sneakerdunk|ebay)\.(?:com|io|net|org)\b/i],
  ["unmasked slab marker", /(?:slab[_-]?unmasked|unmasked[_-]?slab|cert[_-]?visible|barcode[_-]?visible)/i],
  ["private key block", /-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----/],
  ["bearer credential", /\bBearer\s+[A-Za-z0-9._~-]{16,}/],
  ["JWT credential", /\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b/],
  ["GitHub credential", /\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b/],
  ["AWS access key", /\bAKIA[A-Z0-9]{16}\b/],
  ["secret assignment", /\b(?:api[_-]?key|authorization|cookie|password|secret|token)\b\s*[:=]\s*["'][^"'\s]{12,}["']/i],
];
const SECRET_PATTERNS = FORBIDDEN_PUBLIC_PATTERNS.slice(5);
const SNAPSHOT_FORBIDDEN_KEYS = /^(?:authorization|barcode|certNumber|cookie|password|provider|providerId|providerIds|providerSlug|secret|source|sourceId|sourceUrl|token|upstream|upstreamUrl|open|high|low|close|change30dPct|sales7d|trend30d)$/i;

function fail(message) { failures.push(message); }
function warn(message) { warnings.push(message); }
function pass(message) { passes.push(message); }
function relative(file) { return path.relative(root, file).split(path.sep).join("/"); }
function isObject(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
function isNonEmptyString(value) { return typeof value === "string" && value.trim().length > 0; }

function parseTime(value, label, nullable = false) {
  if (nullable && value === null) return null;
  if (!isNonEmptyString(value)) {
    fail(`${label} must be an ISO timestamp${nullable ? " or null" : ""}.`);
    return null;
  }
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) {
    fail(`${label} is not a valid timestamp.`);
    return null;
  }
  return timestamp;
}

function listFiles(entry, { skipBuild = false } = {}) {
  if (!fs.existsSync(entry)) return [];
  const stat = fs.statSync(entry);
  if (stat.isFile()) return [entry];
  const files = [];
  for (const item of fs.readdirSync(entry, { withFileTypes: true })) {
    if (["node_modules", ".git", ".wrangler", ".venv", ".venv-backend", ".preview"].includes(item.name)) continue;
    if (skipBuild && [".next", ".open-next", "out", "dist"].includes(item.name)) continue;
    const child = path.join(entry, item.name);
    if (skipBuild && /^(?:apps\/web\/)?data\/runtime(?:\/|$)/.test(relative(child))) continue;
    files.push(...listFiles(child, { skipBuild }));
  }
  return files;
}

function readText(file) {
  if (!TEXT_EXTENSIONS.has(path.extname(file).toLowerCase())) return null;
  if (fs.statSync(file).size > 64 * 1024 * 1024) {
    fail(`${relative(file)} exceeds the 64 MB text scan limit.`);
    return null;
  }
  try {
    return fs.readFileSync(file, "utf8");
  } catch (error) {
    fail(`Could not read ${relative(file)}: ${error.message}`);
    return null;
  }
}

function scanTextFiles(files, label, { rejectAnyUrl = false, reject24h = false, rejectSourceMaps = false, allowDiscoveryPrivateRoute = false } = {}) {
  let scanned = 0;
  for (const file of files) {
    const portablePath = relative(file);
    if (/(?:slab[_-]?unmasked|unmasked[_-]?slab|cert[_-]?visible|barcode[_-]?visible)/i.test(portablePath)) {
      fail(`${portablePath} has an unsafe public filename.`);
    }
    if (rejectSourceMaps && path.extname(file).toLowerCase() === ".map") {
      fail(`${portablePath} is a public source map.`);
    }
    const content = readText(file);
    if (content === null) continue;
    scanned += 1;
    for (const [name, pattern] of FORBIDDEN_PUBLIC_PATTERNS) {
      const scannedContent = name === "private data path" && allowDiscoveryPrivateRoute
        ? content.replaceAll('"/data/private/"', '""').replaceAll("'/data/private/'", "''")
        : content;
      if (pattern.test(scannedContent)) fail(`${portablePath} contains ${name}.`);
    }
    if (rejectSourceMaps && /sourceMappingURL\s*=/i.test(content)) fail(`${portablePath} references a source map.`);
    if (rejectAnyUrl && /https?:\/\//i.test(content)) {
      fail(`${portablePath} contains an absolute URL. Public data must use relative content-addressed assets.`);
    }
    if (reject24h && /\b24h(?:%|ours?)?\b/i.test(content)) {
      fail(`${portablePath} contains a 24-hour public UI token; use the 1d market window.`);
    }
  }
  if (scanned > 0) pass(`${label}: scanned ${scanned} text files.`);
  else warn(`${label}: no text files were available to scan.`);
}

function scanRepositorySecrets(files) {
  let scanned = 0;
  for (const file of files) {
    const content = readText(file);
    if (content === null) continue;
    scanned += 1;
    for (const [name, pattern] of SECRET_PATTERNS) {
      if (pattern.test(content)) fail(`${relative(file)} contains ${name}.`);
    }
    const portablePath = relative(file);
    if (!/(?:^|\/)(?:package-lock\.json|npm-shrinkwrap\.json|pnpm-lock\.yaml|yarn\.lock)$/i.test(portablePath)) {
      const candidates = content.match(/\b[A-Za-z0-9]{40,80}\b/g) ?? [];
      if (candidates.some((candidate) => /[a-z]/.test(candidate) && /[A-Z]/.test(candidate) && /\d/.test(candidate) && !/^[a-f0-9]+$/i.test(candidate))) {
        fail(`${portablePath} contains a high-entropy credential-like token.`);
      }
    }
  }
  pass(`repository secret scan: checked ${scanned} text files.`);
}

function walkSnapshot(value, pointer = "$") {
  if (typeof value === "string") {
    for (const [name, pattern] of FORBIDDEN_PUBLIC_PATTERNS) {
      if (pattern.test(value)) fail(`${pointer} contains ${name}.`);
    }
    if (/https?:\/\//i.test(value)) fail(`${pointer} contains an upstream URL.`);
    if (/^(?:[A-Za-z]:[\\/]|file:\/\/)/i.test(value)) fail(`${pointer} contains a local filesystem path.`);
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((entry, index) => walkSnapshot(entry, `${pointer}[${index}]`));
    return;
  }
  if (!isObject(value)) return;
  for (const [key, entry] of Object.entries(value)) {
    if (SNAPSHOT_FORBIDDEN_KEYS.test(key)) fail(`${pointer}.${key} is a forbidden public key.`);
    walkSnapshot(entry, `${pointer}.${key}`);
  }
}

function stableSort(value) {
  if (Array.isArray(value)) return value.map(stableSort);
  if (!isObject(value)) return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableSort(value[key])]));
}

function canonicalHash(snapshot) {
  const clone = structuredClone(snapshot);
  if (isObject(clone.generation)) clone.generation.contentSha256 = "";
  return createHash("sha256").update(JSON.stringify(stableSort(clone))).digest("hex");
}

function validateLocalized(value, label, minimumLengths = {}) {
  if (!isObject(value)) {
    fail(`${label} must be a localized object.`);
    return;
  }
  for (const locale of LOCALES) {
    const text = value[locale];
    if (!isNonEmptyString(text)) fail(`${label}.${locale} is missing.`);
    else if (text.trim().length < (minimumLengths[locale] ?? 1)) fail(`${label}.${locale} is too short.`);
  }
}

function validateOptionalLocalized(value, label) {
  if (!isObject(value)) {
    fail(`${label} must be a localized object.`);
    return;
  }
  for (const locale of LOCALES) {
    if (value[locale] !== null && !isNonEmptyString(value[locale])) fail(`${label}.${locale} must be text or null.`);
  }
}

function validateMetric(metric, label, { positive = false } = {}) {
  if (!isObject(metric)) {
    fail(`${label} must be a metric object.`);
    return { asOf: null };
  }
  if (!STATUSES.has(metric.status)) fail(`${label}.status is invalid.`);
  if (metric.value !== null && (typeof metric.value !== "number" || !Number.isFinite(metric.value))) fail(`${label}.value must be finite or null.`);
  if (["accumulating", "unavailable"].includes(metric.status) && metric.value !== null) fail(`${label}.value must be null while ${metric.status}.`);
  if (["ready", "stale"].includes(metric.status) && metric.value === null) fail(`${label}.value is required while ${metric.status}.`);
  if (positive && metric.value !== null && metric.value <= 0) fail(`${label}.value must be positive.`);
  return { asOf: parseTime(metric.asOf, `${label}.asOf`, !["ready", "stale"].includes(metric.status)) };
}

function validateFreshness(metric, label, generatedAt, readyHours, staleHours, enforceSla = true) {
  const { asOf } = validateMetric(metric, label, { positive: true });
  if (asOf === null || generatedAt === null) return;
  const ageHours = (generatedAt - asOf) / 3_600_000;
  if (ageHours < -0.1) fail(`${label}.asOf is after generation.generatedAt.`);
  if (enforceSla && metric.status === "ready" && ageHours > readyHours) fail(`${label} is too old for ready status.`);
  if (enforceSla && metric.status === "stale" && (ageHours <= readyHours || ageHours > staleHours)) fail(`${label} is outside the stale window.`);
  if (!["ready", "stale"].includes(metric.status)) fail(`${label} is not ranking eligible.`);
}

function validateTrackedSales(trackedSales, label) {
  if (!isObject(trackedSales)) {
    fail(`${label} must be an object.`);
    return;
  }
  if (!COVERAGES.has(trackedSales.coverage)) fail(`${label}.coverage is invalid.`);
  validateMetric(trackedSales.valueUsd, `${label}.valueUsd`);
  validateMetric(trackedSales.count, `${label}.count`);
  const asOf = parseTime(trackedSales.asOf, `${label}.asOf`, trackedSales.coverage === "unavailable");
  if (trackedSales.coverage === "unavailable") {
    if (trackedSales.valueUsd?.status !== "unavailable" || trackedSales.count?.status !== "unavailable") {
      fail(`${label} unavailable coverage must use unavailable metrics.`);
    }
  } else if (asOf === null) {
    fail(`${label}.asOf is required for observed coverage.`);
  }
  if (trackedSales.count?.value !== null && (!Number.isInteger(trackedSales.count.value) || trackedSales.count.value < 0)) {
    fail(`${label}.count.value must be a non-negative integer.`);
  }
  if (trackedSales.valueUsd?.value !== null && trackedSales.valueUsd.value < 0) fail(`${label}.valueUsd.value cannot be negative.`);
}

function validateImage(image, label, requireLocalization) {
  if (!isObject(image)) {
    fail(`${label} must be an image object.`);
    return;
  }
  if (!/^\/market-assets\/[a-f0-9]{64}\.(?:webp|png|jpe?g)$/i.test(image.src ?? "")) fail(`${label}.src must be content-addressed and relative.`);
  if (!/^[a-f0-9]{64}$/i.test(image.sha256 ?? "")) fail(`${label}.sha256 must be a SHA-256 digest.`);
  if (image.kind !== "raw_front") fail(`${label}.kind must be raw_front.`);
  if (!Number.isInteger(image.width) || image.width <= 0 || !Number.isInteger(image.height) || image.height <= 0) fail(`${label} dimensions are invalid.`);
  parseTime(image.qcAt, `${label}.qcAt`);
  if (requireLocalization) validateLocalized(image.alt, `${label}.alt`);
  else validateOptionalLocalized(image.alt, `${label}.alt`);
}

function validatePopulationMetric(metric, label) {
  validateMetric(metric, label, { positive: true });
  if (metric?.estimated !== false) fail(`${label}.estimated must be false.`);
}

function validateHistory(history, label, generatedAt) {
  if (!Array.isArray(history)) {
    fail(`${label} must be an array.`);
    return;
  }
  let previous = -Infinity;
  history.forEach((point, index) => {
    const pathLabel = `${label}[${index}]`;
    const at = parseTime(point?.at, `${pathLabel}.at`);
    if (at !== null && at <= previous) fail(`${label} must be strictly chronological.`);
    if (at !== null && generatedAt !== null && at > generatedAt) fail(`${pathLabel}.at is after generation time.`);
    if (at !== null) previous = at;
    if (!STATUSES.has(point?.priceStatus)) fail(`${pathLabel}.priceStatus is invalid.`);
    if (["ready", "stale"].includes(point?.priceStatus)) {
      if (typeof point.priceUsd !== "number" || point.priceUsd <= 0) fail(`${pathLabel}.priceUsd must be positive.`);
    } else if (point?.priceUsd !== null) fail(`${pathLabel}.priceUsd must be null while ${point?.priceStatus}.`);
    if (!COVERAGES.has(point?.salesCoverage)) fail(`${pathLabel}.salesCoverage is invalid.`);
    if (point?.salesCoverage === "unavailable") {
      if (point.trackedSalesValueUsd !== null || point.trackedSalesCount !== null) fail(`${pathLabel} unavailable sales must be null.`);
    } else {
      if (typeof point?.trackedSalesValueUsd !== "number" || point.trackedSalesValueUsd < 0) fail(`${pathLabel}.trackedSalesValueUsd is invalid.`);
      if (!Number.isInteger(point?.trackedSalesCount) || point.trackedSalesCount < 0) fail(`${pathLabel}.trackedSalesCount is invalid.`);
    }
  });
}

function validateCard(card, label, expectedRank, generatedAt, productionMode, requireStory, requireLocalization) {
  if (!isObject(card)) {
    fail(`${label} must be an object.`);
    return;
  }
  if (!/^cmc_[a-z0-9]{12,64}$/i.test(card.id ?? "")) fail(`${label}.id is not an opaque CARDZ ID.`);
  if (card.rank !== expectedRank) fail(`${label}.rank must be ${expectedRank}.`);
  if (!["pokemon", "one-piece", "other"].includes(card.tcg)) fail(`${label}.tcg is invalid.`);
  if (!isNonEmptyString(card.language)) fail(`${label}.language is missing.`);
  if (!isObject(card.collectorNumber)) fail(`${label}.collectorNumber must be an object.`);
  else {
    const display = card.collectorNumber.display ?? "";
    const standalonePromo = /^(?:SM|SWSH)\d{3}$/i.test(display);
    if (!isNonEmptyString(display) || display.includes("#") || (!/[\/-]/.test(display) && !standalonePromo)) fail(`${label}.collectorNumber.display is not a complete collector number.`);
    if (!isNonEmptyString(card.collectorNumber.normalized)) fail(`${label}.collectorNumber.normalized is missing.`);
    if (card.collectorNumber.complete !== true) fail(`${label}.collectorNumber.complete must be true.`);
  }
  const japaneseSetNumber = card.tcg === "pokemon" && card.language === "ja"
    ? card.collectorNumber.display.match(/^(\d{1,4})\/(\d{1,4})$/)
    : null;
  if (japaneseSetNumber && (japaneseSetNumber[1].length !== 3 || japaneseSetNumber[2].length !== 3)) {
    fail(`${label}.collectorNumber.display must preserve Japanese three-digit numbering.`);
  }
  if (/^(?:GG|SV|TG|RC)\d+$/i.test(card.collectorNumber.display)) {
    fail(`${label}.collectorNumber.display is missing its subset denominator.`);
  }
  const allowedIdentity = productionMode ? ["confirmed"] : ["confirmed", "demo_observed"];
  if (!allowedIdentity.includes(card.identityStatus)) fail(`${label}.identityStatus is not publishable.`);
  if (productionMode && requireLocalization) validateLocalized(card.names, `${label}.names`); else validateOptionalLocalized(card.names, `${label}.names`);
  if (productionMode && requireLocalization) validateLocalized(card.sets, `${label}.sets`); else validateOptionalLocalized(card.sets, `${label}.sets`);
  if (productionMode && requireStory) validateLocalized(card.stories, `${label}.stories`, { en: 80, zhTW: 40, zhCN: 40, ja: 40 }); else validateOptionalLocalized(card.stories, `${label}.stories`);
  validateImage(card.image, `${label}.image`, productionMode && requireLocalization);
  validateFreshness(card.pricePsa10, `${label}.pricePsa10`, generatedAt, 30, 48, productionMode);
  validateFreshness(card.populationPsa10, `${label}.populationPsa10`, generatedAt, 48, 168, productionMode);
  validatePopulationMetric(card.populationPsa10, `${label}.populationPsa10`);
  if (typeof card.populationPsa10?.value === "number" && card.populationPsa10.value < 1000) fail(`${label}.populationPsa10.value is below 1000.`);
  validateFreshness(card.marketCap, `${label}.marketCap`, generatedAt, 30, 48, productionMode);
  if (card.marketCap?.status !== card.pricePsa10?.status || card.marketCap?.asOf !== card.pricePsa10?.asOf) fail(`${label}.marketCap freshness must match price.`);
  if ([card.pricePsa10?.value, card.populationPsa10?.value, card.marketCap?.value].every((value) => typeof value === "number")) {
    const expected = card.pricePsa10.value * card.populationPsa10.value;
    if (Math.abs(card.marketCap.value - expected) > Math.max(0.01, expected * 0.000001)) fail(`${label}.marketCap does not equal price times population.`);
  }
  if (!isObject(card.windows) || WINDOWS.some((window) => !isObject(card.windows[window])) || Object.keys(card.windows ?? {}).length !== WINDOWS.length) fail(`${label}.windows must contain 1d, 7d, and 30d only.`);
  else for (const window of WINDOWS) {
    validateMetric(card.windows[window].changePct, `${label}.windows.${window}.changePct`);
    validateTrackedSales(card.windows[window].trackedSales, `${label}.windows.${window}.trackedSales`);
  }
  if (!isObject(card.graderPopulations) || GRADERS.some((grader) => !isObject(card.graderPopulations[grader])) || Object.keys(card.graderPopulations ?? {}).length !== GRADERS.length) fail(`${label}.graderPopulations must contain PSA, BGS, CGC, SGC, and TAG only.`);
  else for (const grader of GRADERS) {
    const population = card.graderPopulations[grader];
    if (!isNonEmptyString(population.topGrade)) fail(`${label}.graderPopulations.${grader}.topGrade is missing.`);
    validatePopulationMetric(population.total, `${label}.graderPopulations.${grader}.total`);
    validatePopulationMetric(population.topGradePopulation, `${label}.graderPopulations.${grader}.topGradePopulation`);
  }
  validateHistory(card.historyDaily, `${label}.historyDaily`, generatedAt);
}

function validateSnapshot(snapshot) {
  const failuresBefore = failures.length;
  if (!isObject(snapshot)) return fail("Public snapshot root must be an object.");
  walkSnapshot(snapshot);
  if (snapshot.schemaVersion !== "2.0.0") fail("schemaVersion must be 2.0.0.");
  if (!isObject(snapshot.generation)) return fail("generation is missing.");
  const generatedAt = parseTime(snapshot.generation.generatedAt, "generation.generatedAt");
  const effectiveAt = parseTime(snapshot.generation.effectiveAt, "generation.effectiveAt");
  if (generatedAt !== null && effectiveAt !== null && (effectiveAt > generatedAt || generatedAt - effectiveAt > 48 * 3_600_000)) fail("generation.effectiveAt must be within 48 hours and not after generatedAt.");
  if (!/^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$/.test(snapshot.generation.id ?? "")) fail("generation.id is missing or unsafe.");
  if (!/^[a-f0-9]{64}$/i.test(snapshot.generation.contentSha256 ?? "")) fail("generation.contentSha256 must be a SHA-256 digest.");
  else if (canonicalHash(snapshot) !== snapshot.generation.contentSha256) fail("generation.contentSha256 does not match canonical content.");
  if (!["demo", "production"].includes(snapshot.generation.mode)) fail("generation.mode is invalid.");
  if (!Array.isArray(snapshot.generation.blockers)) fail("generation.blockers must be an array.");
  const productionMode = snapshot.generation.mode === "production" && snapshot.generation.productionEligible === true;
  const enforceProduction = productionMode || !allowDemo;
  if (!allowDemo && !productionMode) fail("Release gate requires an eligible production generation.");
  if (productionMode && snapshot.generation.blockers.length > 0) fail("Production generation must not declare blockers.");

  const universe = snapshot.universe;
  if (!isObject(universe)) fail("universe is missing.");
  else {
    if (universe.populationMin !== 1000 || universe.grade !== "PSA 10" || universe.rankingMetric !== "psa10_market_cap_usd") fail("universe ranking contract is invalid.");
    if (JSON.stringify(universe.windows) !== JSON.stringify(WINDOWS)) fail("universe.windows must be 1d, 7d, 30d.");
    if (universe.salesCoverage !== "partial") fail("universe.salesCoverage must be partial.");
  }
  if (!isObject(snapshot.currencies)) fail("currencies is missing.");
  else {
    if (snapshot.currencies.base !== "USD" || JSON.stringify(snapshot.currencies.supported) !== JSON.stringify(CURRENCIES)) fail("currency contract is invalid.");
    parseTime(snapshot.currencies.asOf, "currencies.asOf", false);
    for (const currency of CURRENCIES) validateMetric(snapshot.currencies.rates?.[currency], `currencies.rates.${currency}`, { positive: true });
  }

  const top100 = Array.isArray(snapshot.top100) ? snapshot.top100 : [];
  const watchlist = Array.isArray(snapshot.watchlist) ? snapshot.watchlist : [];
  if (top100.length !== 100) fail(`top100 must contain exactly 100 cards, found ${top100.length}.`);
  if (watchlist.length > 400) fail(`watchlist may contain at most ranks 101 to 500, found ${watchlist.length}.`);
  const ids = new Set();
  let previousMarketCap = Infinity;
  [...top100, ...watchlist].forEach((card, index) => {
    const inTop100 = index < top100.length;
    const watchIndex = index - top100.length;
    const label = inTop100 ? `top100[${index}]` : `watchlist[${watchIndex}]`;
    validateCard(card, label, inTop100 ? index + 1 : watchIndex + 101, generatedAt, enforceProduction, inTop100, inTop100);
    if (ids.has(card?.id)) fail(`${label}.id is duplicated.`); else ids.add(card?.id);
    if (typeof card?.marketCap?.value === "number") {
      if (card.marketCap.value > previousMarketCap) fail("top100 and watchlist are not ordered by descending market cap.");
      previousMarketCap = card.marketCap.value;
    }
  });

  if (!isObject(snapshot.coverage)) fail("coverage is missing.");
  else {
    if (snapshot.coverage.top100Count !== top100.length || snapshot.coverage.watchlistCount !== watchlist.length) fail("coverage rank counts are inconsistent.");
    for (const window of WINDOWS) {
      const changeReady = top100.filter((card) => card?.windows?.[window]?.changePct?.status === "ready").length;
      const salesReady = top100.filter((card) => card?.windows?.[window]?.trackedSales?.valueUsd?.status === "ready").length;
      if (snapshot.coverage.changeReady?.[window] !== changeReady) fail(`coverage.changeReady.${window} is inconsistent.`);
      if (snapshot.coverage.salesReady?.[window] !== salesReady) fail(`coverage.salesReady.${window} is inconsistent.`);
    }
    for (const grader of GRADERS) {
      const ready = top100.filter((card) => card?.graderPopulations?.[grader]?.topGradePopulation?.status === "ready").length;
      if (snapshot.coverage.graderPopulationReady?.[grader] !== ready) fail(`coverage.graderPopulationReady.${grader} is inconsistent.`);
    }
    const confirmed = top100.filter((card) => card?.identityStatus === "confirmed").length;
    if (snapshot.coverage.completeIdentityCount !== confirmed) fail("coverage.completeIdentityCount is inconsistent.");
    for (const locale of LOCALES) {
      const count = top100.filter((card) => isNonEmptyString(card?.stories?.[locale])).length;
      if (snapshot.coverage.localizedStoryCount?.[locale] !== count) fail(`coverage.localizedStoryCount.${locale} is inconsistent.`);
    }
  }
  const snapshotFailures = failures.length - failuresBefore;
  if (snapshotFailures === 0) pass(`snapshot v2 contract: checked ${top100.length} Top 100 cards and ${watchlist.length} watchlist cards.`);
  else warn(`snapshot v2 contract: ${snapshotFailures} validation failures.`);
}

console.log("CARDZ public release verification");
console.log(`Mode: ${allowDemo ? "structural demo check" : "strict production gate"}`);

scanRepositorySecrets(listFiles(root, { skipBuild: true }));
const publicSourceFiles = listFiles(path.join(root, "apps", "web"), { skipBuild: true });
scanTextFiles(publicSourceFiles, "public source", { reject24h: true, allowDiscoveryPrivateRoute: true });
const bulkSnapshotRoute = path.join(root, "apps", "web", "src", "app", "api", "snapshot", "route.ts");
if (fs.existsSync(bulkSnapshotRoute)) fail("apps/web/src/app/api/snapshot/route.ts exposes a public bulk snapshot route.");
scanTextFiles(listFiles(path.join(root, "data", "public")), "public data", { rejectAnyUrl: true, reject24h: true });

try {
  validateSnapshot(JSON.parse(fs.readFileSync(SNAPSHOT_PATH, "utf8")));
} catch (error) {
  fail(`Could not parse public snapshot: ${error.message}`);
}

const publicBuildEntries = [
  "apps/web/.next/static",
  "apps/web/out",
  "apps/web/.open-next/assets",
].map((entry) => path.join(root, entry));
const serverBuildEntries = [
  "apps/web/.next/server",
  "apps/web/.open-next/worker.js",
  "apps/web/.open-next/server-functions",
].map((entry) => path.join(root, entry));
const publicBuildFiles = publicBuildEntries.flatMap((entry) => listFiles(entry));
const serverBuildFiles = serverBuildEntries.flatMap((entry) => listFiles(entry));
if (publicBuildFiles.length > 0) scanTextFiles(publicBuildFiles, "browser-exposed build", { reject24h: true, rejectSourceMaps: true, allowDiscoveryPrivateRoute: true });
else if (requireBuild) fail("public build output is missing.");
else warn("public build: no output found, run npm run build before release.");
if (serverBuildFiles.length > 0) scanTextFiles(serverBuildFiles, "private Worker build", { reject24h: true, allowDiscoveryPrivateRoute: true });

for (const message of passes) console.log(`PASS ${message}`);
for (const message of warnings) console.warn(`WARN ${message}`);
const groups = new Map();
for (const message of failures) {
  const category = message.replace(/((?:top100|watchlist))\[\d+\]/g, "$1[*]").replace(/found \d+/g, "found *");
  const group = groups.get(category) ?? { count: 0, example: message };
  group.count += 1;
  groups.set(category, group);
}
for (const [category, group] of groups) console.error(`FAIL x${group.count} ${category}${group.example === category ? "" : ` Example: ${group.example}`}`);
const summary = `Summary: ${passes.length} passed, ${warnings.length} warnings, ${failures.length} failures in ${groups.size} categories.`;
if (failures.length > 0) console.error(summary); else console.log(summary);
if (failures.length > 0) process.exitCode = 1;
