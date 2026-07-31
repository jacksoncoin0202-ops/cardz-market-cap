import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { copyFile, mkdtemp, mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const RELAXED_POLICY_SHA256 = "5c7c7aa96f5669379d3436ce2d3c04c2dfe1972a5feba70c319ec91e066f4125";
const EFFECTIVE_AT = "2026-07-31T00:00:00Z";

function stableSort(value) {
  if (Array.isArray(value)) return value.map(stableSort);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(Object.keys(value).sort().map((key) => [key, stableSort(value[key])]));
}

function reseal(snapshot) {
  snapshot.generation.contentSha256 = "";
  snapshot.generation.contentSha256 = createHash("sha256")
    .update(JSON.stringify(stableSort(snapshot)))
    .digest("hex");
  return snapshot;
}

function metric(value, status = "ready") {
  return { value, status, asOf: status === "ready" || status === "stale" ? EFFECTIVE_AT : null };
}

function card(rank, sales = 5) {
  const imageHash = "a".repeat(64);
  const localized = { en: "Fixture", zhTW: "測試", zhCN: "测试", ja: "テスト" };
  const unavailablePopulation = { value: null, status: "unavailable", asOf: null, estimated: false };
  return {
    id: `cmc_${rank.toString(36).padStart(12, "a")}`,
    rank,
    marketRank: rank,
    viewRank: rank,
    tcg: "pokemon",
    cardLanguage: "en",
    collectorNumber: { display: "001/100", normalized: "001/100", complete: true },
    identityStatus: "provisional",
    names: localized,
    sets: localized,
    stories: {
      en: `Fixture ${"E".repeat(80)}`,
      zhTW: `測試${"繁".repeat(40)}`,
      zhCN: `测试${"简".repeat(40)}`,
      ja: `テスト${"日".repeat(40)}`,
    },
    image: {
      src: `/market-assets/${imageHash}.webp`, sha256: imageHash, kind: "raw_front", width: 429, height: 600, qcAt: EFFECTIVE_AT, alt: localized,
    },
    pricePsa10: metric(10),
    populationPsa10: { ...metric(1000), estimated: false },
    marketCap: metric(10000),
    windows: Object.fromEntries(["1d", "7d", "30d"].map((window) => [window, {
      changePct: metric(null, "unavailable"),
      trackedSales: window === "30d"
        ? { valueUsd: metric(100), count: metric(sales), coverage: "partial", asOf: EFFECTIVE_AT }
        : { valueUsd: metric(null, "unavailable"), count: metric(null, "unavailable"), coverage: "unavailable", asOf: null },
    }])),
    graderPopulations: Object.fromEntries(["PSA", "BGS", "CGC", "SGC", "TAG"].map((grader) => [grader, {
      topGrade: "10", total: unavailablePopulation, topGradePopulation: unavailablePopulation,
    }])),
    historyDaily: [],
  };
}

function snapshot(sales = 5) {
  const cards = Array.from({ length: 101 }, (_unused, index) => card(index + 1, sales));
  return reseal({
    schemaVersion: "2.0.0",
    generation: {
      id: "relaxed_profile_fixture",
      generatedAt: EFFECTIVE_AT,
      effectiveAt: EFFECTIVE_AT,
      contentSha256: "",
      mode: "production",
      productionEligible: true,
      blockers: [],
      releaseProfile: "relaxed-launch-v1",
      policySha256: RELAXED_POLICY_SHA256,
      dbFingerprint: "b".repeat(64),
      evaluationId: 1,
    },
    universe: { populationMin: 1000, grade: "PSA 10", rankingMetric: "psa10_market_cap_usd", windows: ["1d", "7d", "30d"], salesCoverage: "partial" },
    currencies: {
      base: "USD", supported: ["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"], asOf: EFFECTIVE_AT,
      rates: Object.fromEntries(["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"].map((currency, index) => [currency, metric(index + 1)])),
    },
    top100: cards.slice(0, 100),
    watchlist: cards.slice(100),
    coverage: {
      claim: "verified-top-100", requestedCount: 100, verifiedCount: 101, top100Count: 100, watchlistCount: 1, publicCardCount: 101,
      changeReady: { "1d": 0, "7d": 0, "30d": 0 },
      salesReady: { "1d": 0, "7d": 0, "30d": 101 },
      graderPopulationReady: { PSA: 0, BGS: 0, CGC: 0, SGC: 0, TAG: 0 },
      completeIdentityCount: 101,
      localizedStoryCount: { en: 101, zhTW: 101, zhCN: 101, ja: 101 },
    },
  });
}

function legacyStrictSnapshot() {
  const value = snapshot();
  for (const cardValue of [...value.top100, ...value.watchlist]) cardValue.identityStatus = "confirmed";
  delete value.generation.releaseProfile;
  delete value.generation.policySha256;
  delete value.generation.dbFingerprint;
  delete value.generation.evaluationId;
  value.coverage.salesReady = { "1d": 0, "7d": 0, "30d": 100 };
  value.coverage.completeIdentityCount = 100;
  value.coverage.localizedStoryCount = { en: 100, zhTW: 100, zhCN: 100, ja: 100 };
  return reseal(value);
}

async function fixtureDirectory() {
  const directory = await mkdtemp(path.join(tmpdir(), "cardz-verify-public-profile-"));
  await Promise.all([
    mkdir(path.join(directory, "scripts"), { recursive: true }),
    mkdir(path.join(directory, "config"), { recursive: true }),
    mkdir(path.join(directory, "data", "public"), { recursive: true }),
  ]);
  await Promise.all([
    copyFile(path.join(root, "scripts", "verify-public.mjs"), path.join(directory, "scripts", "verify-public.mjs")),
    copyFile(path.join(root, "config", "data-routing.json"), path.join(directory, "config", "data-routing.json")),
  ]);
  execFileSync("git", ["init", "-q"], { cwd: directory });
  return directory;
}

function verify(directory) {
  return spawnSync(process.execPath, [path.join(directory, "scripts", "verify-public.mjs")], {
    cwd: directory,
    encoding: "utf8",
  });
}

test("verify-public accepts the profile-bound relaxed N-card release and rejects four sales", async () => {
  const directory = await fixtureDirectory();
  try {
    const snapshotPath = path.join(directory, "data", "public", "seed-snapshot.json");
    await writeFile(snapshotPath, `${JSON.stringify(snapshot(), null, 2)}\n`);
    const valid = verify(directory);
    assert.equal(valid.status, 0, `${valid.stdout}\n${valid.stderr}`);

    await writeFile(snapshotPath, `${JSON.stringify(snapshot(4), null, 2)}\n`);
    const insufficientSales = verify(directory);
    assert.notEqual(insufficientSales.status, 0);
    assert.match(insufficientSales.stderr, /requires at least 5 pure PSA10 tracked sales in 30d/);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});

test("verify-public preserves the unbound strict legacy contract", async () => {
  const directory = await fixtureDirectory();
  try {
    await writeFile(
      path.join(directory, "data", "public", "seed-snapshot.json"),
      `${JSON.stringify(legacyStrictSnapshot(), null, 2)}\n`,
    );
    const result = verify(directory);
    assert.equal(result.status, 0, `${result.stdout}\n${result.stderr}`);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
});
