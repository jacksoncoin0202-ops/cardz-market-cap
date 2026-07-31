#!/usr/bin/env node

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
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
const ROUTING_CONFIG_PATH = path.join(root, "config", "data-routing.json");
const STRICT_RELEASE_PROFILE = "strict-v1";
const RELAXED_RELEASE_PROFILE = "relaxed-launch-v1";
const RELEASE_PROFILE_IDS = new Set([STRICT_RELEASE_PROFILE, RELAXED_RELEASE_PROFILE]);
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

function listGitVisibleFiles() {
  try {
    const output = execFileSync(
      "git",
      ["ls-files", "-co", "--exclude-standard", "-z"],
      { cwd: root, encoding: "utf8", maxBuffer: 64 * 1024 * 1024 },
    );
    return [...new Set(output.split("\0").filter(Boolean))]
      .map((file) => path.join(root, file))
      .filter((file) => fs.existsSync(file) && fs.statSync(file).isFile());
  } catch (error) {
    fail(`Could not enumerate the Git-visible repository boundary: ${error.message}`);
    return [];
  }
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
      const candidates = [...content.matchAll(/\b[A-Za-z0-9]{40,80}\b/g)];
      const looksLikeCredential = candidates.some((match) => {
        const candidate = match[0];
        const index = match.index ?? 0;
        const before = content.slice(Math.max(0, index - 1), index);
        const after = content.slice(index + candidate.length, index + candidate.length + 4);
        const quotedPropertyKey = (before === '"' || before === "'")
          && new RegExp(`^${before}\\s*:`).test(after);
        const explicitSafeAlphabet = candidate === "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";
        return !quotedPropertyKey
          && !explicitSafeAlphabet
          && /[a-z]/.test(candidate)
          && /[A-Z]/.test(candidate)
          && /\d/.test(candidate)
          && !/^[a-f0-9]+$/i.test(candidate);
      });
      if (looksLikeCredential) {
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

// `data_routing.release_profile_sha256` hashes a Python canonical JSON payload.
// JSON.parse preserves the policy values but loses the lexical `.0` on the one
// approved ratio field, so keep that field's Python float representation here.
function pythonCanonicalJson(value, key = "") {
  if (value === null || typeof value === "boolean" || typeof value === "string") return JSON.stringify(value);
  if (typeof value === "number") {
    if (!Number.isFinite(value)) throw new Error("policy contains a non-finite number");
    if (key === "priceSpreadMaximumRatio" && Number.isInteger(value)) return `${value}.0`;
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map((entry) => pythonCanonicalJson(entry)).join(",")}]`;
  if (!isObject(value)) throw new Error("policy contains an unsupported value");
  return `{${Object.keys(value).sort().map((entry) => (
    `${JSON.stringify(entry)}:${pythonCanonicalJson(value[entry], entry)}`
  )).join(",")}}`;
}

function releaseProfileSha256(profileId, policy) {
  const payload = pythonCanonicalJson({ releaseProfile: profileId, policy });
  return createHash("sha256").update(`${payload}\n`, "utf8").digest("hex");
}

function loadReleaseProfiles() {
  try {
    const routing = JSON.parse(fs.readFileSync(ROUTING_CONFIG_PATH, "utf8"));
    if (!isObject(routing.releaseProfiles)) throw new Error("releaseProfiles is missing");
    const profiles = {};
    for (const profileId of RELEASE_PROFILE_IDS) {
      const policy = routing.releaseProfiles[profileId];
      if (!isObject(policy)) throw new Error(`${profileId} is missing or invalid`);
      profiles[profileId] = policy;
    }
    return profiles;
  } catch (error) {
    fail(`Could not load central release policy: ${error.message}`);
    return null;
  }
}

function policyInteger(policy, field, fallback) {
  const value = policy?.[field];
  if (!Number.isInteger(value) || value <= 0) {
    fail(`Central release policy ${field} must be a positive integer.`);
    return fallback;
  }
  return value;
}

function policyIdentityStatuses(policy) {
  const statuses = policy?.allowedIdentityStatuses;
  if (!Array.isArray(statuses) || statuses.length === 0 || statuses.some((value) => !isNonEmptyString(value))) {
    fail("Central release policy allowedIdentityStatuses is invalid.");
    return new Set(["confirmed"]);
  }
  return new Set(statuses);
}

function releaseContext(generation) {
  const hasProfile = Object.hasOwn(generation, "releaseProfile");
  const hasPolicyHash = Object.hasOwn(generation, "policySha256");
  const hasDatabaseBinding = Object.hasOwn(generation, "dbFingerprint") || Object.hasOwn(generation, "evaluationId");
  const profileBound = hasProfile || hasPolicyHash || hasDatabaseBinding;
  let profileId = STRICT_RELEASE_PROFILE;
  if (hasProfile) {
    if (!isNonEmptyString(generation.releaseProfile)) fail("generation.releaseProfile is invalid.");
    else profileId = generation.releaseProfile;
  }
  if (!RELEASE_PROFILE_IDS.has(profileId)) {
    fail("generation.releaseProfile is unknown.");
    profileId = STRICT_RELEASE_PROFILE;
  }

  const profiles = loadReleaseProfiles();
  const policy = profiles?.[profileId] ?? null;
  if (profileBound) {
    if (!hasProfile || !hasPolicyHash) fail("generation release profile and policy SHA-256 must be bound together.");
    const declaredHash = String(generation.policySha256 ?? "").toLowerCase();
    if (!/^[a-f0-9]{64}$/.test(declaredHash)) fail("generation.policySha256 must be a SHA-256 digest.");
    else if (policy !== null && declaredHash !== releaseProfileSha256(profileId, policy)) {
      fail("generation.policySha256 does not match the central release policy.");
    }
    if (!/^[a-f0-9]{64}$/i.test(generation.dbFingerprint ?? "")) {
      fail("generation.dbFingerprint must be a SHA-256 digest for a profile-bound release.");
    }
    if (!Number.isInteger(generation.evaluationId) || generation.evaluationId <= 0) {
      fail("generation.evaluationId must be a positive integer for a profile-bound release.");
    }
  }
  if (profileId === RELAXED_RELEASE_PROFILE && !profileBound) {
    fail("relaxed-launch-v1 must declare its release profile and policy SHA-256.");
  }

  const strict = profileId === STRICT_RELEASE_PROFILE;
  const requestedCount = policyInteger(policy, "requestedCount", 100);
  if (requestedCount !== 100) fail("Central release policy requestedCount must remain 100.");
  const publicCardsMaximum = policyInteger(policy, "publicCardsMaximum", strict ? 500 : 1000);
  const priceFreshnessHours = policyInteger(policy, "priceFreshnessHoursMaximum", strict ? 48 : 720);
  const lastGoodHours = policyInteger(policy, "lastGoodMaximumHours", strict ? 48 : 720);
  const populationFreshnessHours = policyInteger(policy, "populationFreshnessHoursMaximum", 168);
  const salesMinimum = policyInteger(policy, "trackedPsa10Sales30dMinimumInclusive", strict ? 10 : 5);
  const minimumVerifiedCount = policyInteger(policy, "minimumVerifiedCount", strict ? 1 : 100);
  return {
    profileId,
    profileBound,
    relaxed: profileId === RELAXED_RELEASE_PROFILE,
    allowedIdentityStatuses: policyIdentityStatuses(policy),
    requestedCount,
    publicCardsMaximum,
    watchlistMaximum: Math.max(0, publicCardsMaximum - requestedCount),
    priceMaximumHours: Math.min(priceFreshnessHours, lastGoodHours),
    populationMaximumHours: populationFreshnessHours,
    salesMinimum,
    minimumVerifiedCount,
  };
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

function validateFreshness(
  metric,
  label,
  generatedAt,
  readyHours,
  staleHours,
  enforceSla = true,
  { allowEitherStatusWithinMaximum = false } = {},
) {
  const { asOf } = validateMetric(metric, label, { positive: true });
  if (asOf === null || generatedAt === null) return;
  const ageHours = (generatedAt - asOf) / 3_600_000;
  if (ageHours < -0.1) fail(`${label}.asOf is after generation.generatedAt.`);
  if (enforceSla && allowEitherStatusWithinMaximum) {
    if (ageHours > staleHours) fail(`${label} exceeds the ${staleHours}h release-profile freshness limit.`);
    if (!["ready", "stale"].includes(metric.status)) fail(`${label} is not ranking eligible.`);
    return;
  }
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
  validateMetric(metric, label);
  if (typeof metric?.value === "number" && metric.value < 0) fail(`${label}.value cannot be negative.`);
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

function validateCard(card, label, expectedRank, generatedAt, productionMode, requireStory, requireLocalization, release) {
  if (!isObject(card)) {
    fail(`${label} must be an object.`);
    return;
  }
  if (!/^cmc_[a-z0-9]{12,64}$/i.test(card.id ?? "")) fail(`${label}.id is not an opaque CARDZ ID.`);
  if (card.rank !== expectedRank) fail(`${label}.rank must be ${expectedRank}.`);
  if (!["pokemon", "one-piece", "other"].includes(card.tcg)) fail(`${label}.tcg is invalid.`);
  const cardLanguage = card.cardLanguage;
  const validCardLanguage = ["en", "ja", "ko", "zhCN", "zhTW"].includes(cardLanguage);
  if (cardLanguage !== undefined && cardLanguage !== null && !validCardLanguage) fail(`${label}.cardLanguage is invalid.`);
  if (productionMode && !validCardLanguage) fail(`${label}.cardLanguage is required in production.`);
  if (!isObject(card.collectorNumber)) fail(`${label}.collectorNumber must be an object.`);
  else {
    const display = card.collectorNumber.display ?? "";
    const standalonePromo = /^(?:SM|SWSH)\d{3}$/i.test(display);
    if (!isNonEmptyString(display) || display.includes("#") || (!/[\/-]/.test(display) && !standalonePromo)) fail(`${label}.collectorNumber.display is not a complete collector number.`);
    if (!isNonEmptyString(card.collectorNumber.normalized)) fail(`${label}.collectorNumber.normalized is missing.`);
    if (card.collectorNumber.complete !== true) fail(`${label}.collectorNumber.complete must be true.`);
  }
  const japaneseSetNumber = card.tcg === "pokemon" && cardLanguage === "ja"
    ? card.collectorNumber.display.match(/^(\d{1,4})\/(\d{1,4})$/)
    : null;
  if (japaneseSetNumber && (japaneseSetNumber[1].length !== 3 || japaneseSetNumber[2].length !== 3)) {
    fail(`${label}.collectorNumber.display must preserve Japanese three-digit numbering.`);
  }
  if (/^(?:GG|SV|TG|RC)\d+$/i.test(card.collectorNumber.display)) {
    fail(`${label}.collectorNumber.display is missing its subset denominator.`);
  }
  const allowedIdentity = productionMode
    ? release.allowedIdentityStatuses
    : new Set(["confirmed", "demo_observed", ...(release.relaxed ? ["provisional"] : [])]);
  if (!allowedIdentity.has(card.identityStatus)) fail(`${label}.identityStatus is not publishable.`);
  if (productionMode && requireLocalization) validateLocalized(card.names, `${label}.names`); else validateOptionalLocalized(card.names, `${label}.names`);
  if (productionMode && requireLocalization) validateLocalized(card.sets, `${label}.sets`); else validateOptionalLocalized(card.sets, `${label}.sets`);
  if (productionMode && requireStory) validateLocalized(card.stories, `${label}.stories`, { en: 80, zhTW: 40, zhCN: 40, ja: 40 }); else validateOptionalLocalized(card.stories, `${label}.stories`);
  validateImage(card.image, `${label}.image`, productionMode && requireLocalization);
  const relaxedFreshness = release.relaxed
    ? { allowEitherStatusWithinMaximum: true }
    : undefined;
  validateFreshness(
    card.pricePsa10,
    `${label}.pricePsa10`,
    generatedAt,
    release.relaxed ? release.priceMaximumHours : 30,
    release.relaxed ? release.priceMaximumHours : 48,
    productionMode,
    relaxedFreshness,
  );
  validateFreshness(
    card.populationPsa10,
    `${label}.populationPsa10`,
    generatedAt,
    release.relaxed ? release.populationMaximumHours : 48,
    release.relaxed ? release.populationMaximumHours : 168,
    productionMode,
    relaxedFreshness,
  );
  validatePopulationMetric(card.populationPsa10, `${label}.populationPsa10`);
  if (typeof card.populationPsa10?.value === "number" && card.populationPsa10.value < 1000) fail(`${label}.populationPsa10.value is below 1000.`);
  validateFreshness(
    card.marketCap,
    `${label}.marketCap`,
    generatedAt,
    release.relaxed ? release.priceMaximumHours : 30,
    release.relaxed ? release.priceMaximumHours : 48,
    productionMode,
    relaxedFreshness,
  );
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
  if (productionMode && release.profileBound) {
    const sales = card.windows?.["30d"]?.trackedSales;
    const count = sales?.count;
    if (
      !isObject(sales)
      || !["partial", "stale"].includes(sales.coverage)
      || !isObject(count)
      || !["ready", "stale"].includes(count.status)
      || !Number.isInteger(count.value)
      || count.value < release.salesMinimum
    ) {
      fail(`${label} requires at least ${release.salesMinimum} pure PSA10 tracked sales in 30d.`);
    }
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
  const release = releaseContext(snapshot.generation);
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
  if (release.relaxed) {
    if (top100.length + watchlist.length > release.publicCardsMaximum) {
      fail(`release-profile card capacity is ${release.publicCardsMaximum}, found ${top100.length + watchlist.length}.`);
    }
    if (watchlist.length > release.watchlistMaximum) {
      fail(`watchlist may contain at most ${release.watchlistMaximum} cards for ${release.profileId}, found ${watchlist.length}.`);
    }
  } else if (watchlist.length > 400) {
    fail(`watchlist may contain at most ranks 101 to 500, found ${watchlist.length}.`);
  }
  const ids = new Set();
  let previousMarketCap = Infinity;
  [...top100, ...watchlist].forEach((card, index) => {
    const inTop100 = index < top100.length;
    const watchIndex = index - top100.length;
    const label = inTop100 ? `top100[${index}]` : `watchlist[${watchIndex}]`;
    validateCard(
      card,
      label,
      inTop100 ? index + 1 : watchIndex + 101,
      release.relaxed ? effectiveAt : generatedAt,
      enforceProduction,
      inTop100,
      inTop100,
      release,
    );
    if (ids.has(card?.id)) fail(`${label}.id is duplicated.`); else ids.add(card?.id);
    if (typeof card?.marketCap?.value === "number") {
      if (card.marketCap.value > previousMarketCap) fail("top100 and watchlist are not ordered by descending market cap.");
      previousMarketCap = card.marketCap.value;
    }
  });

  if (!isObject(snapshot.coverage)) fail("coverage is missing.");
  else {
    if (snapshot.coverage.top100Count !== top100.length || snapshot.coverage.watchlistCount !== watchlist.length) fail("coverage rank counts are inconsistent.");
    const coverageCards = release.relaxed ? [...top100, ...watchlist] : top100;
    if (release.relaxed) {
      if (snapshot.coverage.verifiedCount !== coverageCards.length) {
        fail("coverage.verifiedCount must equal the relaxed release card count.");
      }
      if (coverageCards.length < release.minimumVerifiedCount) {
        fail(`relaxed release must contain at least ${release.minimumVerifiedCount} verified cards.`);
      }
      if (snapshot.coverage.publicCardCount !== coverageCards.length) {
        fail("coverage.publicCardCount must equal the relaxed release card count.");
      }
    }
    for (const window of WINDOWS) {
      const changeReady = coverageCards.filter((card) => card?.windows?.[window]?.changePct?.status === "ready").length;
      const salesReady = release.relaxed
        ? coverageCards.filter((card) => ["partial", "stale"].includes(card?.windows?.[window]?.trackedSales?.coverage)).length
        : coverageCards.filter((card) => card?.windows?.[window]?.trackedSales?.valueUsd?.status === "ready").length;
      if (snapshot.coverage.changeReady?.[window] !== changeReady) fail(`coverage.changeReady.${window} is inconsistent.`);
      if (snapshot.coverage.salesReady?.[window] !== salesReady) fail(`coverage.salesReady.${window} is inconsistent.`);
    }
    for (const grader of GRADERS) {
      const ready = coverageCards.filter((card) => card?.graderPopulations?.[grader]?.topGradePopulation?.status === "ready").length;
      if (snapshot.coverage.graderPopulationReady?.[grader] !== ready) fail(`coverage.graderPopulationReady.${grader} is inconsistent.`);
    }
    const completeIdentity = release.relaxed
      ? coverageCards.filter((card) => card?.collectorNumber?.complete === true).length
      : coverageCards.filter((card) => card?.identityStatus === "confirmed").length;
    if (snapshot.coverage.completeIdentityCount !== completeIdentity) fail("coverage.completeIdentityCount is inconsistent.");
    for (const locale of LOCALES) {
      const count = coverageCards.filter((card) => isNonEmptyString(card?.stories?.[locale])).length;
      if (snapshot.coverage.localizedStoryCount?.[locale] !== count) fail(`coverage.localizedStoryCount.${locale} is inconsistent.`);
    }
  }
  const snapshotFailures = failures.length - failuresBefore;
  if (snapshotFailures === 0) pass(`snapshot v2 contract: checked ${top100.length} Top 100 cards and ${watchlist.length} watchlist cards.`);
  else warn(`snapshot v2 contract: ${snapshotFailures} validation failures.`);
}

console.log("CARDZ public release verification");
console.log(`Mode: ${allowDemo ? "structural demo check" : "strict production gate"}`);

scanRepositorySecrets(listGitVisibleFiles());
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
