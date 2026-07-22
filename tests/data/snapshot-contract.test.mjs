import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { publicSnapshotContentSha256, validatePublicSnapshot } from "../../packages/market-data/dist/index.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function sortObject(value) {
  if (Array.isArray(value)) return value.map(sortObject);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortObject(value[key])]));
  }
  return value;
}

async function snapshot() {
  return JSON.parse(await readFile(path.join(root, "data/public/seed-snapshot.json"), "utf8"));
}

function expectedJapanesePromoNamespace(setName) {
  for (const namespace of ["SV-P", "SM-P", "XY-P", "BW-P", "DPt-P", "DP-P", "PCG-P", "ADV-P", "S-P"]) {
    if (new RegExp(`(^|[^A-Z])${namespace.replace("-", "\\-")}([^A-Z]|$)`, "i").test(setName)) return namespace;
  }
  if (!/promo|campaign|special box|stamp box|master battle|munch/i.test(setName)) return null;
  if (/sword and shield/i.test(setName)) return "S-P";
  if (/scarlet and violet/i.test(setName)) return "SV-P";
  if (/sun and moon/i.test(setName)) return "SM-P";
  if (/\bxy\b/i.test(setName)) return "XY-P";
  return null;
}

test("demo seed satisfies the public data contract", async () => {
  const value = await snapshot();
  assert.deepEqual(validatePublicSnapshot(value), []);
  assert.equal(value.schemaVersion, "2.0.0");
  assert.equal(value.generation.effectiveAt, value.generation.generatedAt);
  assert.deepEqual(value.universe.windows, ["1d", "7d", "30d"]);
  assert.equal(value.top100.length, 100);
  assert.ok(value.top100.every((card) => card.populationPsa10.value >= 1000));
  assert.ok(value.top100.every((card) => card.populationPsa10.estimated === false));
  assert.ok(value.top100.every((card) => ["ready", "stale"].includes(card.pricePsa10.status)));
  assert.ok(value.top100.every((card) => card.collectorNumber.complete));
  assert.ok(value.top100.every((card) => card.image.kind === "raw_front"));
  assert.ok(value.top100.every((card) => card.image.src.startsWith("/market-assets/")));
  assert.ok(value.top100.every((card) => ["1d", "7d", "30d"].every((window) => window in card.windows)));
  assert.ok(value.top100.every((card) => ["PSA", "BGS", "CGC", "SGC"].every((grader) => grader in card.graderPopulations)));
  assert.ok(value.top100.every((card) => Object.values(card.graderPopulations).every((grader) =>
    ["1d", "7d", "30d"].every((window) => window in grader.topGradePopulationChangePct))));
  assert.ok(value.top100.every((card) => !("change30dPct" in card) && !("sales7d" in card)));
  assert.ok(value.top100.every((card) => !/\/(?:ADV-P|PCG-P|DP-P|DPT-P|BW-P|XY-P|SM-P|SV-P|S-P)$/i.test(card.collectorNumber.display) || card.language === "ja"));
  assert.ok(value.top100.every((card) => !/\/(?:SVP|MEP)$/.test(card.collectorNumber.display) || card.language === "en"));
  assert.ok(value.top100.every((card) => card.tcg !== "pokemon" || card.language !== "ja" || !/^\d+\/\d+$/.test(card.collectorNumber.display) || /^\d{3}\/\d{3}$/.test(card.collectorNumber.display)));
  for (const card of [...value.top100, ...value.watchlist]) {
    if (card.tcg !== "pokemon" || card.language !== "ja") continue;
    const expected = expectedJapanesePromoNamespace(card.sets.en ?? "");
    if (expected !== null) assert.ok(card.collectorNumber.display.toLowerCase().endsWith(`/${expected.toLowerCase()}`), `${card.rank} ${card.collectorNumber.display} does not match ${expected}`);
  }
  assert.ok([...value.top100, ...value.watchlist].some((card) => card.collectorNumber.display === "023/MEP"));
  assert.ok([...value.top100, ...value.watchlist].some((card) => card.collectorNumber.display === "024/MEP"));
  assert.deepEqual(
    Object.fromEntries(value.top100.filter((card) => [30, 32, 43, 45, 48, 51, 52, 53, 55, 60, 62, 72, 83, 88, 94].includes(card.rank)).map((card) => [card.rank, card.collectorNumber.display])),
    { 30: "78/73", 32: "001/025", 43: "020/019", 45: "007/025", 48: "083/067", 51: "080/073", 52: "GG69/GG70", 53: "001/030", 55: "GG44/GG70", 60: "094/087", 62: "SV49/SV94", 72: "010/032", 83: "082/072", 88: "SV107/SV122", 94: "012/025" },
  );
  const hashes = [...value.top100, ...value.watchlist].map((card) => card.image.sha256);
  assert.equal(new Set(hashes).size, hashes.length);
});

test("content hash is deterministic across runtimes", async () => {
  const value = await snapshot();
  const expected = value.generation.contentSha256;
  value.generation.contentSha256 = "";
  const actual = createHash("sha256").update(JSON.stringify(sortObject(value))).digest("hex");
  assert.equal(actual, expected);
});

test("unready metrics never contain a fake zero", async () => {
  const value = await snapshot();
  for (const card of [...value.top100, ...value.watchlist]) {
    const windowMetrics = Object.values(card.windows).flatMap((window) => [
      window.changePct,
      window.trackedSales.valueUsd,
      window.trackedSales.count,
    ]);
    const populationMetrics = Object.values(card.graderPopulations).flatMap((grader) => [
      grader.total,
      grader.topGradePopulation,
      ...Object.values(grader.topGradePopulationChangePct),
    ]);
    for (const metric of [...windowMetrics, ...populationMetrics]) {
      if (metric.status === "accumulating" || metric.status === "unavailable") {
        assert.equal(metric.value, null);
      }
    }
  }
});

test("production validation blocks the honest demo seed", async () => {
  const value = await snapshot();
  const errors = validatePublicSnapshot(value, { production: true });
  assert.ok(errors.some((error) => error.includes("generation mode")));
  assert.ok(errors.some((error) => error.includes("release blocked")));
  assert.ok(errors.some((error) => error.includes("identity is not confirmed")));
});

test("production validation measures price freshness from the run effective time", async () => {
  const value = await snapshot();
  value.generation.mode = "production";
  value.generation.productionEligible = true;
  value.generation.blockers = [];
  value.generation.contentSha256 = publicSnapshotContentSha256(value);
  const errors = validatePublicSnapshot(value, { production: true });
  assert.ok(errors.some((error) => error.includes("price exceeds 48h freshness SLA")));
});

test("watchlist may contain zero to 400 contiguous eligible cards without padding", async () => {
  const value = await snapshot();
  value.watchlist = [];
  value.coverage.watchlistCount = 0;
  value.generation.contentSha256 = publicSnapshotContentSha256(value);
  assert.deepEqual(validatePublicSnapshot(value), []);
});

test("watchlist rejects rank gaps and more than 400 cards", async () => {
  const value = await snapshot();
  const source = value.watchlist[0] ?? value.top100.at(-1);
  value.watchlist = Array.from({ length: 401 }, (_, index) => ({
    ...structuredClone(source),
    id: `cmc_watch${String(index).padStart(7, "0")}`,
    rank: index + 101,
    marketCap: { ...source.marketCap, value: source.marketCap.value - index },
  }));
  value.watchlist[1].rank = 103;
  value.watchlist[1].marketCap.value = source.marketCap.value + 1;
  value.coverage.watchlistCount = value.watchlist.length;
  value.generation.contentSha256 = publicSnapshotContentSha256(value);
  const errors = validatePublicSnapshot(value);
  assert.ok(errors.includes("watchlist must contain at most 400 cards"));
  assert.ok(errors.includes("watchlist ranks must start at 101 and be contiguous"));
  assert.ok(errors.includes("top100 and watchlist are not ordered by market cap"));
});

test("generation IDs cannot escape the versioned snapshot namespace", async () => {
  const value = await snapshot();
  value.generation.id = "..";
  value.generation.contentSha256 = publicSnapshotContentSha256(value);
  assert.ok(validatePublicSnapshot(value).includes("generation id is unsafe"));
});
