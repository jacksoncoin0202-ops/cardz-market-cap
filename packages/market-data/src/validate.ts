import {
  COVERAGE_STATUSES,
  GRADERS,
  MARKET_STATUSES,
  MARKET_WINDOWS,
  SNAPSHOT_SCHEMA_VERSION,
  type CoverageStatus,
  type DailyHistoryPoint,
  type LocalizedText,
  type MarketMetric,
  type PublicCard,
  type PublicMarketSnapshot,
} from "./schema.js";
import { isOpaquePublicId } from "./id.js";
import { createHash } from "node:crypto";

const FORBIDDEN_PUBLIC_TEXT = [
  /https?:\/\//i,
  /source[_-]?url/i,
  /provider[_-]?id/i,
  /upstream[_-]?(?:url|id)/i,
];

function assert(condition: unknown, message: string, errors: string[]): void {
  if (!condition) errors.push(message);
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value as Record<string, unknown>)
        .sort()
        .map((key) => [key, stableValue((value as Record<string, unknown>)[key])]),
    );
  }
  return value;
}

export function publicSnapshotContentSha256(snapshot: PublicMarketSnapshot): string {
  const normalized = structuredClone(snapshot);
  normalized.generation.contentSha256 = "";
  return createHash("sha256").update(JSON.stringify(stableValue(normalized))).digest("hex");
}

export function publicSnapshotContentHashMatches(snapshot: PublicMarketSnapshot): boolean {
  return /^[0-9a-f]{64}$/.test(snapshot.generation.contentSha256)
    && publicSnapshotContentSha256(snapshot) === snapshot.generation.contentSha256;
}

function metricAgeHours(metric: MarketMetric, effectiveAt: string): number | null {
  if (metric.asOf === null) return null;
  const end = Date.parse(effectiveAt);
  const observed = Date.parse(metric.asOf);
  if (!Number.isFinite(end) || !Number.isFinite(observed)) return null;
  return Math.max(0, (end - observed) / 3_600_000);
}

function validateMetric(metric: MarketMetric, path: string, errors: string[]): void {
  assert(metric !== null && typeof metric === "object", `${path} must be an object`, errors);
  if (metric === null || typeof metric !== "object") return;
  assert(MARKET_STATUSES.includes(metric.status), `${path}.status is invalid`, errors);
  if (metric.status === "unavailable" || metric.status === "accumulating") {
    assert(metric.value === null, `${path}.value must be null while ${metric.status}`, errors);
  }
  if (metric.status === "ready" || metric.status === "stale") {
    assert(typeof metric.value === "number" && Number.isFinite(metric.value), `${path}.value must be finite`, errors);
    assert(typeof metric.asOf === "string" && metric.asOf.length > 0, `${path}.asOf is required`, errors);
  }
}

function validateLocalized(value: LocalizedText, path: string, errors: string[]): void {
  for (const locale of ["en", "zhTW", "zhCN", "ja"] as const) {
    const text = value[locale];
    assert(text === null || (typeof text === "string" && text.trim().length > 0), `${path}.${locale} is invalid`, errors);
  }
}

function validateCoverage(value: CoverageStatus, path: string, errors: string[]): void {
  assert(COVERAGE_STATUSES.includes(value), `${path} is invalid`, errors);
}

function validateHistoryPoint(point: DailyHistoryPoint, path: string, errors: string[]): void {
  assert(typeof point.at === "string" && Number.isFinite(Date.parse(point.at)), `${path}.at is invalid`, errors);
  assert(MARKET_STATUSES.includes(point.priceStatus), `${path}.priceStatus is invalid`, errors);
  if (point.priceStatus === "ready" || point.priceStatus === "stale") {
    assert(typeof point.priceUsd === "number" && point.priceUsd > 0, `${path}.priceUsd must be positive`, errors);
  } else {
    assert(point.priceUsd === null, `${path}.priceUsd must be null while ${point.priceStatus}`, errors);
  }
  validateCoverage(point.salesCoverage, `${path}.salesCoverage`, errors);
  if (point.salesCoverage === "unavailable") {
    assert(point.trackedSalesValueUsd === null, `${path}.trackedSalesValueUsd must be null`, errors);
    assert(point.trackedSalesCount === null, `${path}.trackedSalesCount must be null`, errors);
  } else {
    assert(
      typeof point.trackedSalesValueUsd === "number" && point.trackedSalesValueUsd >= 0,
      `${path}.trackedSalesValueUsd is invalid`,
      errors,
    );
    assert(
      Number.isInteger(point.trackedSalesCount) && (point.trackedSalesCount ?? -1) >= 0,
      `${path}.trackedSalesCount is invalid`,
      errors,
    );
  }
}

function validateCard(card: PublicCard, path: string, errors: string[]): void {
  assert(isOpaquePublicId(card.id), `${path}.id is not opaque`, errors);
  assert(Number.isInteger(card.rank) && card.rank > 0, `${path}.rank is invalid`, errors);
  assert(card.collectorNumber.complete, `${path} collector number is incomplete`, errors);
  assert(card.collectorNumber.display.trim().length > 0, `${path} collector number is empty`, errors);
  if (/\/(?:ADV-P|PCG-P|DP-P|DPT-P|BW-P|XY-P|SM-P|SV-P|S-P)$/i.test(card.collectorNumber.display)) {
    assert(card.language === "ja", `${path} Japanese promo namespace has conflicting language`, errors);
  }
  if (/\/(?:SVP|MEP)$/.test(card.collectorNumber.display)) {
    assert(card.language === "en", `${path} English promo namespace has conflicting language`, errors);
  }
  const pokemonSetNumber = card.tcg === "pokemon" ? card.collectorNumber.display.match(/^(\d{1,4})\/(\d{1,4})$/) : null;
  if (pokemonSetNumber && card.language === "ja") {
    assert(
      pokemonSetNumber[1]?.length === 3 && pokemonSetNumber[2]?.length === 3,
      `${path} Japanese set number must preserve three-digit numerator and denominator`,
      errors,
    );
  }
  assert(
    !/^(?:GG|SV|TG|RC)\d+$/i.test(card.collectorNumber.display),
    `${path} subset collector number is missing its denominator`,
    errors,
  );
  assert(card.populationPsa10.value !== null && card.populationPsa10.value >= 1000, `${path} population is below 1000`, errors);
  assert(card.populationPsa10.estimated === false, `${path} population is estimated`, errors);
  assert(card.pricePsa10.status === "ready" || card.pricePsa10.status === "stale", `${path} price is unavailable`, errors);
  assert(card.marketCap.status === card.pricePsa10.status, `${path} market-cap freshness differs from price`, errors);
  if (
    typeof card.pricePsa10.value === "number" &&
    typeof card.populationPsa10.value === "number" &&
    typeof card.marketCap.value === "number"
  ) {
    const expected = card.pricePsa10.value * card.populationPsa10.value;
    assert(Math.abs(card.marketCap.value - expected) < 0.01, `${path} market cap formula is inconsistent`, errors);
  }
  assert(card.image.kind === "raw_front", `${path}.image.kind must be raw_front`, errors);
  assert(card.image.src.startsWith("/market-assets/"), `${path}.image.src is outside public assets`, errors);
  assert(/^[0-9a-f]{64}$/.test(card.image.sha256), `${path}.image.sha256 is invalid`, errors);
  assert(card.image.width > 0 && card.image.height > 0, `${path}.image dimensions are invalid`, errors);
  validateLocalized(card.names, `${path}.names`, errors);
  validateLocalized(card.sets, `${path}.sets`, errors);
  validateLocalized(card.stories, `${path}.stories`, errors);
  validateMetric(card.pricePsa10, `${path}.pricePsa10`, errors);
  validateMetric(card.populationPsa10, `${path}.populationPsa10`, errors);
  validateMetric(card.marketCap, `${path}.marketCap`, errors);

  assert(
    Object.keys(card.windows).length === MARKET_WINDOWS.length && MARKET_WINDOWS.every((window) => window in card.windows),
    `${path}.windows is incomplete`,
    errors,
  );
  for (const window of MARKET_WINDOWS) {
    const metrics = card.windows[window];
    assert(metrics !== undefined, `${path}.windows.${window} is missing`, errors);
    if (metrics === undefined) continue;
    validateMetric(metrics.changePct, `${path}.windows.${window}.changePct`, errors);
    validateMetric(metrics.trackedSales.valueUsd, `${path}.windows.${window}.trackedSales.valueUsd`, errors);
    validateMetric(metrics.trackedSales.count, `${path}.windows.${window}.trackedSales.count`, errors);
    validateCoverage(metrics.trackedSales.coverage, `${path}.windows.${window}.trackedSales.coverage`, errors);
    if (metrics.trackedSales.coverage === "unavailable") {
      assert(metrics.trackedSales.valueUsd.status === "unavailable", `${path}.windows.${window} sales value must be unavailable`, errors);
      assert(metrics.trackedSales.count.status === "unavailable", `${path}.windows.${window} sales count must be unavailable`, errors);
    }
  }

  assert(
    Object.keys(card.graderPopulations).length === GRADERS.length && GRADERS.every((grader) => grader in card.graderPopulations),
    `${path}.graderPopulations is incomplete`,
    errors,
  );
  for (const grader of GRADERS) {
    const population = card.graderPopulations[grader];
    assert(population !== undefined, `${path}.graderPopulations.${grader} is missing`, errors);
    if (population === undefined) continue;
    assert(population.topGrade.trim().length > 0, `${path}.graderPopulations.${grader}.topGrade is empty`, errors);
    validateMetric(population.total, `${path}.graderPopulations.${grader}.total`, errors);
    validateMetric(population.topGradePopulation, `${path}.graderPopulations.${grader}.topGradePopulation`, errors);
    assert(population.total.estimated === false, `${path}.graderPopulations.${grader}.total is estimated`, errors);
    assert(population.topGradePopulation.estimated === false, `${path}.graderPopulations.${grader}.topGradePopulation is estimated`, errors);
    assert(
      Object.keys(population.topGradePopulationChangePct).length === MARKET_WINDOWS.length
        && MARKET_WINDOWS.every((window) => window in population.topGradePopulationChangePct),
      `${path}.graderPopulations.${grader}.topGradePopulationChangePct is incomplete`,
      errors,
    );
    for (const window of MARKET_WINDOWS) {
      validateMetric(
        population.topGradePopulationChangePct[window],
        `${path}.graderPopulations.${grader}.topGradePopulationChangePct.${window}`,
        errors,
      );
    }
  }

  assert(Array.isArray(card.historyDaily), `${path}.historyDaily must be an array`, errors);
  let previous = -Infinity;
  for (const [index, point] of card.historyDaily.entries()) {
    validateHistoryPoint(point, `${path}.historyDaily[${index}]`, errors);
    const at = Date.parse(point.at);
    assert(at > previous, `${path}.historyDaily must be strictly chronological`, errors);
    previous = at;
  }
}

export interface ValidationOptions {
  production?: boolean;
}

export function validatePublicSnapshot(
  snapshot: PublicMarketSnapshot,
  options: ValidationOptions = {},
): string[] {
  const errors: string[] = [];
  assert(snapshot.schemaVersion === SNAPSHOT_SCHEMA_VERSION, "schemaVersion is unsupported", errors);
  assert(publicSnapshotContentHashMatches(snapshot), "generation content hash is inconsistent", errors);
  assert(
    /^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$/.test(snapshot.generation.id),
    "generation id is unsafe",
    errors,
  );
  assert(snapshot.top100.length === 100, "top100 must contain exactly 100 cards", errors);
  assert(snapshot.watchlist.length <= 400, "watchlist must contain at most 400 cards", errors);
  assert(snapshot.universe.windows.join(",") === MARKET_WINDOWS.join(","), "universe.windows is invalid", errors);
  const allCards = [...snapshot.top100, ...snapshot.watchlist];
  assert(new Set(allCards.map((card) => card.id)).size === allCards.length, "public card IDs must be unique", errors);
  assert(snapshot.top100.every((card, index) => card.rank === index + 1), "top100 ranks must be contiguous", errors);
  assert(snapshot.watchlist.every((card, index) => card.rank === index + 101), "watchlist ranks must start at 101 and be contiguous", errors);
  assert(
    allCards.every((card, index, cards) => {
      const previous = index > 0 ? cards[index - 1] : undefined;
      return previous === undefined || (previous.marketCap.value ?? 0) >= (card.marketCap.value ?? 0);
    }),
    "top100 and watchlist are not ordered by market cap",
    errors,
  );
  snapshot.top100.forEach((card, index) => validateCard(card, `top100[${index}]`, errors));
  snapshot.watchlist.forEach((card, index) => validateCard(card, `watchlist[${index}]`, errors));

  const serialized = JSON.stringify(snapshot);
  for (const pattern of FORBIDDEN_PUBLIC_TEXT) {
    assert(!pattern.test(serialized), `public snapshot contains forbidden token ${pattern}`, errors);
  }

  if (options.production) {
    assert(snapshot.generation.mode === "production", "generation mode is not production", errors);
    assert(snapshot.generation.productionEligible, "generation is release blocked", errors);
    assert(snapshot.generation.blockers.length === 0, "production generation has blockers", errors);
    for (const [index, card] of snapshot.top100.entries()) {
      assert(card.identityStatus === "confirmed", `top100[${index}] identity is not confirmed`, errors);
      for (const field of [card.names, card.sets, card.stories]) {
        assert(Object.values(field).every((value) => typeof value === "string" && value.length > 0), `top100[${index}] localization is incomplete`, errors);
      }
      const stories = Object.values(card.stories).filter((value): value is string => typeof value === "string");
      assert(stories.every((story) => story.trim().length >= 80), `top100[${index}] story is too short`, errors);
      assert(new Set(stories.map((story) => story.trim())).size === 4, `top100[${index}] stories are not independently localized`, errors);
      const genericStory = /(trader market view|炒家市場觀察|受到市場關注，重點不只是單張報價)/i;
      assert(stories.every((story) => !genericStory.test(story)), `top100[${index}] story uses a rejected generic template`, errors);
      const priceAge = metricAgeHours(card.pricePsa10, snapshot.generation.effectiveAt);
      const populationAge = metricAgeHours(card.populationPsa10, snapshot.generation.effectiveAt);
      assert(priceAge !== null && priceAge <= 48, `top100[${index}] price exceeds 48h freshness SLA`, errors);
      assert(populationAge !== null && populationAge <= 168, `top100[${index}] population exceeds 7d freshness SLA`, errors);
    }
    for (const [index, card] of snapshot.watchlist.entries()) {
      assert(card.identityStatus === "confirmed", `watchlist[${index}] identity is not confirmed`, errors);
      for (const field of [card.names, card.sets]) {
        assert(Object.values(field).every((value) => typeof value === "string" && value.length > 0), `watchlist[${index}] identity localization is incomplete`, errors);
      }
    }
  }

  return errors;
}

export function assertPublicSnapshot(
  snapshot: PublicMarketSnapshot,
  options: ValidationOptions = {},
): void {
  const errors = validatePublicSnapshot(snapshot, options);
  if (errors.length > 0) throw new Error(errors.join("\n"));
}
