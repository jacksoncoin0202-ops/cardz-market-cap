import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { mkdtemp, mkdir, readFile, readdir, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";
import test from "node:test";

const execute = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

function sortObject(value) {
  if (Array.isArray(value)) return value.map(sortObject);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value).sort().map((key) => [key, sortObject(value[key])]),
    );
  }
  return value;
}

function snapshotHash(snapshot) {
  const clone = structuredClone(snapshot);
  clone.generation.contentSha256 = "";
  return createHash("sha256").update(JSON.stringify(sortObject(clone))).digest("hex");
}

function receiptSnapshotHash(snapshot) {
  const clone = structuredClone(snapshot);
  clone.generation.contentSha256 = "";
  clone.generation.qcReceiptSha256 = "";
  return createHash("sha256").update(JSON.stringify(sortObject(clone))).digest("hex");
}

function sha256(value) {
  return createHash("sha256").update(value).digest("hex");
}

function pythonCompatibleCanonicalJson(value, field = "") {
  if (Array.isArray(value)) {
    return `[${value.map((item) => pythonCompatibleCanonicalJson(item, field)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${pythonCompatibleCanonicalJson(value[key], key)}`)
      .join(",")}}`;
  }
  if (field === "priceSpreadMaximumRatio" && typeof value === "number" && Number.isInteger(value)) {
    return value.toFixed(1);
  }
  return JSON.stringify(value);
}

async function configuredReleaseProfile(id) {
  const routing = JSON.parse(await readFile(path.join(root, "config", "data-routing.json"), "utf8"));
  const policy = routing.releaseProfiles?.[id];
  assert.ok(policy && typeof policy === "object", `release profile ${id} must exist`);
  return {
    id,
    policy,
    policySha256: sha256(`${pythonCompatibleCanonicalJson({ releaseProfile: id, policy })}\n`),
  };
}

function metric(value, status, asOf, extra = {}) {
  return { value, status, asOf, ...extra };
}

async function productionFixture(directory) {
  const profile = await configuredReleaseProfile("strict-v1");
  const databaseFingerprint = "c".repeat(64);
  const evaluationId = 1;
  const seed = JSON.parse(await readFile(path.join(root, "data/public/seed-snapshot.json"), "utf8"));
  const effectiveAt = "2026-07-29T00:00:00Z";
  const card = structuredClone(seed.top100[0]);
  card.rank = 1;
  card.marketRank = 1;
  card.viewRank = 1;
  card.identityStatus = "confirmed";
  card.names = {
    en: "Fixture Card",
    zhTW: "測試卡",
    zhCN: "测试卡",
    ja: "テストカード",
  };
  card.sets = {
    en: "Fixture Set",
    zhTW: "測試系列",
    zhCN: "测试系列",
    ja: "テストセット",
  };
  card.cardLanguage = "en";
  const printingKey = [
    String(card.tcg).trim().toLocaleLowerCase(),
    "en",
    String(card.sets.en).trim().toLocaleLowerCase(),
    String(card.collectorNumber.normalized).trim().toLocaleLowerCase(),
    "fixture-edition",
    "fixture-parallel",
    "fixture-finish",
  ].join("|");
  card.printingIdentity = {
    setName: card.sets.en,
    collectorNumber: card.collectorNumber.normalized,
    editionCode: "fixture-edition",
    parallelCode: "fixture-parallel",
    finishCode: "fixture-finish",
    cardLanguage: "en",
    canonicalPrintingSha256: sha256(printingKey),
    evidenceSha256: "f".repeat(64),
  };
  card.stories = {
    en: `English fixture evidence ${"E".repeat(90)}`,
    zhTW: `繁體中文測試證據 ${"繁".repeat(90)}`,
    zhCN: `简体中文测试证据 ${"简".repeat(90)}`,
    ja: `日本語のテスト証跡 ${"日".repeat(90)}`,
  };
  card.pricePsa10 = metric(100, "ready", effectiveAt);
  card.populationPsa10 = metric(1000, "ready", effectiveAt, { estimated: false });
  card.marketCap = metric(100000, "ready", effectiveAt);
  for (const window of ["1d", "7d", "30d"]) {
    card.windows[window] = {
      changePct: metric(null, "accumulating", null),
      marketCapChangePct: metric(null, "accumulating", null),
      trackedSalesChangePct: metric(null, "accumulating", null),
      trackedSales: window === "30d"
        ? {
            valueUsd: metric(1000, "ready", effectiveAt),
            count: metric(10, "ready", effectiveAt),
            coverage: "partial",
            asOf: effectiveAt,
          }
        : {
            valueUsd: metric(null, "unavailable", null),
            count: metric(null, "unavailable", null),
            coverage: "unavailable",
            asOf: null,
          },
    };
  }
  for (const grader of ["PSA", "BGS", "CGC", "SGC", "TAG"]) {
    card.graderPopulations[grader] = {
      topGrade: "10",
      total: metric(null, "unavailable", null, { estimated: false }),
      topGradePopulation: metric(null, "unavailable", null, { estimated: false }),
      topGradePopulationChangePct: Object.fromEntries(
        ["1d", "7d", "30d"].map((window) => [
          window,
          metric(null, "unavailable", null),
        ]),
      ),
    };
  }
  card.historyDaily = [];

  const snapshot = {
    ...seed,
    generation: {
      id: "publisher_fixture_20260729",
      releaseProfile: profile.id,
      policySha256: profile.policySha256,
      dbFingerprint: databaseFingerprint,
      evaluationId,
      generatedAt: "2026-07-29T00:30:00Z",
      effectiveAt,
      contentSha256: "",
      qcReceiptSha256: "",
      mode: "production",
      productionEligible: true,
      blockers: [],
    },
    coverage: {
      ...seed.coverage,
      claim: "verified-top-n",
      requestedCount: 100,
      verifiedCount: 1,
      top100Count: 1,
      watchlistCount: 0,
    },
    currencies: {
      ...seed.currencies,
      asOf: effectiveAt,
      rates: Object.fromEntries(
        seed.currencies.supported.map((currency, index) => [
          currency,
          metric(currency === "USD" ? 1 : index + 1, "ready", effectiveAt),
        ]),
      ),
    },
    top100: [card],
    watchlist: [],
  };
  const mediaAssets = [];
  const cardMedia = {};
  for (const [variant, suffix, width, height] of [
    ["base", "", card.image.width, card.image.height],
    ["200", "_200", 200, 280],
    ["600", "_600", 429, 600],
  ]) {
    const filename = `${card.image.sha256}${suffix}.webp`;
    const payload = await readFile(path.join(root, "data/public/market-assets", filename));
    const media = {
      key: `market-assets/${filename}`,
      sha256: sha256(payload),
      width,
      height,
    };
    cardMedia[variant] = media;
    mediaAssets.push({
      ...media,
      variant,
      baseSha256: card.image.sha256,
    });
  }
  mediaAssets.sort((left, right) => left.key.localeCompare(right.key));
  const dbQcReceipt = {
    schemaVersion: 1,
    runId: "daily_20260729T010000000000Z",
    releaseProfile: profile.id,
    policySha256: profile.policySha256,
    asOf: "2026-07-29T00:45:00Z",
    status: "passed",
    readOnly: true,
    database: "cardz_market_cap",
    databaseFingerprint,
    evaluationId,
    universeCandidateSha256: "a".repeat(64),
    reportSha256: "b".repeat(64),
    counts: {
      qualified: 1,
      monitoring: 0,
      releaseReadyQualified: 1,
      releaseBlockedQualified: 0,
      minimumReleaseEligible: 1,
    },
    releaseGate: {
      eligible: true,
      perCardExclude: false,
      minimumEligible: 1,
      eligibleCardCount: 1,
      blockerCount: 0,
      blockerCardCount: 0,
      blockers: {},
      globalBlockers: [],
    },
    reviewQueueCounts: {},
  };
  const receipt = {
    schemaVersion: 1,
    runId: dbQcReceipt.runId,
    releaseProfile: profile.id,
    policySha256: profile.policySha256,
    databaseFingerprint,
    evaluationId,
    generationId: snapshot.generation.id,
    dbQcReceiptSha256: "",
    universeCandidateSha256: dbQcReceipt.universeCandidateSha256,
    checkedAt: "2026-07-29T01:00:00Z",
    status: "passed",
    claim: "verified-top-n",
    requestedCount: 100,
    verifiedCount: 1,
    cards: [{
      id: card.id,
      imageSha256: card.image.sha256,
      decision: "passed",
      evidenceSha256: "e".repeat(64),
      media: cardMedia,
    }],
    media: {
      prefix: "market-assets/",
      assets: mediaAssets,
    },
    blockers: [],
  };
  return writeFixture(directory, snapshot, receipt, dbQcReceipt);
}

async function relaxedProductionFixture(directory) {
  const strict = await productionFixture(directory);
  const profile = await configuredReleaseProfile("relaxed-launch-v1");
  const databaseFingerprint = "d".repeat(64);
  const evaluationId = 2;
  const source = strict.snapshot.top100[0];
  const cards = Array.from({ length: 100 }, (_unused, index) => {
    const card = structuredClone(source);
    const ordinal = index + 1;
    const editionCode = `fixture-edition-${ordinal}`;
    const parallelCode = "fixture-parallel";
    const finishCode = "fixture-finish";
    const printingKey = [
      String(card.tcg).trim().toLocaleLowerCase(),
      "en",
      String(card.sets.en).trim().toLocaleLowerCase(),
      String(card.collectorNumber.normalized).trim().toLocaleLowerCase(),
      editionCode,
      parallelCode,
      finishCode,
    ].join("|");
    card.id = `cmc_${ordinal.toString(16).padStart(24, "0")}`;
    card.rank = ordinal;
    card.marketRank = ordinal;
    card.viewRank = ordinal;
    card.identityStatus = ordinal === 1 ? "provisional" : "confirmed";
    card.printingIdentity = {
      ...card.printingIdentity,
      editionCode,
      parallelCode,
      finishCode,
      canonicalPrintingSha256: sha256(printingKey),
    };
    const price = 100 - index / 1000;
    card.pricePsa10.value = price;
    card.marketCap.value = price * card.populationPsa10.value;
    card.windows["30d"].trackedSales.count.value = 5;
    return card;
  });
  const dbQcReceipt = {
    ...strict.dbQcReceipt,
    releaseProfile: profile.id,
    policySha256: profile.policySha256,
    databaseFingerprint,
    evaluationId,
    counts: {
      qualified: 101,
      monitoring: 0,
      releaseReadyQualified: 100,
      releaseBlockedQualified: 1,
      minimumReleaseEligible: 100,
    },
    releaseGate: {
      eligible: true,
      perCardExclude: true,
      minimumEligible: 100,
      eligibleCardCount: 100,
      blockerCount: 1,
      blockerCardCount: 1,
      blockers: { image_rejected: 1 },
      globalBlockers: [],
    },
  };
  const snapshot = {
    ...strict.snapshot,
    generation: {
      ...strict.snapshot.generation,
      id: "publisher_relaxed_fixture_20260729",
      releaseProfile: profile.id,
      policySha256: profile.policySha256,
      dbFingerprint: databaseFingerprint,
      evaluationId,
      contentSha256: "",
      qcReceiptSha256: "",
    },
    coverage: {
      ...strict.snapshot.coverage,
      claim: "verified-top-100",
      requestedCount: 100,
      verifiedCount: cards.length,
      top100Count: cards.length,
      watchlistCount: 0,
      publicCardCount: cards.length,
    },
    top100: cards,
    watchlist: [],
  };
  const receipt = {
    ...strict.receipt,
    releaseProfile: profile.id,
    policySha256: profile.policySha256,
    databaseFingerprint,
    evaluationId,
    generationId: snapshot.generation.id,
    claim: "verified-top-100",
    requestedCount: 100,
    verifiedCount: cards.length,
    cards: cards.map((card) => ({
      id: card.id,
      imageSha256: card.image.sha256,
      decision: "passed",
      evidenceSha256: "e".repeat(64),
      media: strict.receipt.cards[0].media,
    })),
  };
  return writeFixture(directory, snapshot, receipt, dbQcReceipt);
}

async function writeFixture(directory, snapshot, receipt, dbQcReceipt, options = {}) {
  await mkdir(directory, { recursive: true });
  const dbQcReceiptPayload = `${JSON.stringify(dbQcReceipt, null, 2)}\n`;
  const dbQc = {
    runId: dbQcReceipt.runId,
    receiptSha256: sha256(dbQcReceiptPayload),
    database: dbQcReceipt.database,
    universeCandidateSha256: dbQcReceipt.universeCandidateSha256,
    ...(dbQcReceipt.releaseProfile === undefined ? {} : {
      releaseProfile: dbQcReceipt.releaseProfile,
      policySha256: dbQcReceipt.policySha256,
      databaseFingerprint: dbQcReceipt.databaseFingerprint,
      evaluationId: dbQcReceipt.evaluationId,
    }),
  };
  snapshot.generation.dbQc = dbQc;
  receipt.runId = dbQc.runId;
  receipt.dbQc = dbQc;
  receipt.dbQcReceiptSha256 = dbQc.receiptSha256;
  receipt.universeCandidateSha256 = dbQc.universeCandidateSha256;
  receipt.snapshotContentSha256 = receiptSnapshotHash(snapshot);
  const receiptPayload = `${JSON.stringify(receipt, null, 2)}\n`;
  if (!options.keepQcHash) snapshot.generation.qcReceiptSha256 = sha256(receiptPayload);
  snapshot.generation.contentSha256 = snapshotHash(snapshot);
  const snapshotPath = path.join(directory, "snapshot.json");
  const receiptPath = path.join(directory, "qc-receipt.json");
  const dbQcReceiptPath = path.join(directory, "db-qc-receipt.json");
  await writeFile(snapshotPath, `${JSON.stringify(snapshot, null, 2)}\n`);
  await writeFile(receiptPath, receiptPayload);
  await writeFile(dbQcReceiptPath, dbQcReceiptPayload);
  return {
    snapshot,
    receipt,
    dbQcReceipt,
    snapshotPath,
    receiptPath,
    dbQcReceiptPath,
  };
}

async function publish(fixture, out, extra = []) {
  return execute(
    "node",
    [
      "pipelines/publish-snapshot.mjs",
      "--snapshot",
      fixture.snapshotPath,
      "--qc-receipt",
      fixture.receiptPath,
      "--db-qc-receipt",
      fixture.dbQcReceiptPath,
      "--out",
      out,
      ...extra,
    ],
    { cwd: root },
  );
}

test("official publisher binds production snapshot, QC receipt, and all three media files", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "cardz-official-publisher-"));
  const fixture = await productionFixture(path.join(directory, "input"));
  const out = path.join(directory, "out");
  const { stdout } = await publish(fixture, out);
  const result = JSON.parse(stdout.trim());
  const pointer = JSON.parse(await readFile(path.join(out, "latest.json"), "utf8"));

  assert.equal(pointer.generationId, fixture.snapshot.generation.id);
  assert.equal(pointer.sha256, fixture.snapshot.generation.contentSha256);
  assert.equal(pointer.qcReceiptSha256, fixture.snapshot.generation.qcReceiptSha256);
  assert.equal(
    fixture.receipt.snapshotContentSha256,
    receiptSnapshotHash(fixture.snapshot),
  );
  assert.equal(pointer.qcReceiptKey, `generations/${pointer.generationId}/qc-receipt.json`);
  assert.equal(pointer.dbQcReceiptKey, `generations/${pointer.generationId}/db-qc-receipt.json`);
  assert.deepEqual(pointer.dbQc, fixture.snapshot.generation.dbQc);
  assert.equal(pointer.media.hashes.length, 1);
  assert.equal(pointer.media.assets.length, 3);
  assert.deepEqual(pointer.media.assets, fixture.receipt.media.assets);
  assert.equal(new Set(pointer.media.assets.map((asset) => asset.key)).size, 3);
  assert.equal(result.assetCount, 3);
  assert.equal(
    await readFile(path.join(out, pointer.qcReceiptKey), "utf8"),
    await readFile(fixture.receiptPath, "utf8"),
  );
  for (const suffix of [".webp", "_200.webp", "_600.webp"]) {
    assert.ok(
      await readFile(
        path.join(out, "generations", pointer.generationId, "assets", `${fixture.snapshot.top100[0].image.sha256}${suffix}`),
      ),
    );
  }
});

test("relaxed publisher accepts provisional cards and a card-scoped DB QC receipt", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "cardz-relaxed-publisher-"));
  const fixture = await relaxedProductionFixture(path.join(directory, "input"));
  const out = path.join(directory, "out");
  const { stdout } = await publish(fixture, out);
  const result = JSON.parse(stdout.trim());
  const pointer = JSON.parse(await readFile(path.join(out, "latest.json"), "utf8"));

  assert.equal(fixture.snapshot.top100.length, 100);
  assert.equal(fixture.snapshot.top100[0].identityStatus, "provisional");
  assert.equal(fixture.snapshot.top100[0].windows["30d"].trackedSales.count.value, 5);
  assert.equal(fixture.snapshot.coverage.verifiedCount, 100);
  assert.equal(fixture.receipt.verifiedCount, 100);
  assert.equal(fixture.dbQcReceipt.counts.releaseBlockedQualified, 1);
  assert.equal(fixture.dbQcReceipt.releaseGate.perCardExclude, true);
  assert.equal(pointer.releaseProfile, "relaxed-launch-v1");
  assert.equal(pointer.policySha256, fixture.snapshot.generation.policySha256);
  assert.equal(pointer.databaseFingerprint, fixture.snapshot.generation.dbFingerprint);
  assert.equal(pointer.evaluationId, fixture.snapshot.generation.evaluationId);
  assert.equal(result.releaseProfile, "relaxed-launch-v1");
  assert.equal(result.assetCount, 3);
});

test("relaxed publisher rejects a DB receipt with a mismatched policy binding", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "cardz-relaxed-publisher-mismatch-"));
  const fixture = await relaxedProductionFixture(path.join(directory, "input"));
  fixture.dbQcReceipt.policySha256 = "0".repeat(64);
  const rewritten = await writeFixture(
    path.dirname(fixture.snapshotPath),
    fixture.snapshot,
    fixture.receipt,
    fixture.dbQcReceipt,
  );
  const out = path.join(directory, "out");
  const sentinel = '{"generationId":"last-good"}\n';
  await mkdir(out);
  await writeFile(path.join(out, "latest.json"), sentinel);
  await assert.rejects(
    publish(rewritten, out),
    /canonical DB QC receipt release binding is invalid/,
  );
  assert.equal(await readFile(path.join(out, "latest.json"), "utf8"), sentinel);
});

test("every strict publisher failure leaves an existing latest pointer byte-identical", async (context) => {
  const cases = [
    ["demo", async (fixture) => {
      fixture.snapshot.generation.mode = "demo";
      return writeFixture(path.dirname(fixture.snapshotPath), fixture.snapshot, fixture.receipt, fixture.dbQcReceipt);
    }, /generation mode is not production/],
    ["blockers", async (fixture) => {
      fixture.snapshot.generation.productionEligible = false;
      fixture.snapshot.generation.blockers = ["qc_pending"];
      return writeFixture(path.dirname(fixture.snapshotPath), fixture.snapshot, fixture.receipt, fixture.dbQcReceipt);
    }, /generation is release blocked|production generation has blockers/],
    ["DB QC status", async (fixture) => {
      fixture.dbQcReceipt.status = "failed";
      fixture.dbQcReceipt.releaseGate.eligible = false;
      return writeFixture(path.dirname(fixture.snapshotPath), fixture.snapshot, fixture.receipt, fixture.dbQcReceipt);
    }, /canonical DB QC receipt contract is invalid/],
    ["receipt hash", async (fixture) => {
      fixture.snapshot.generation.qcReceiptSha256 = "0".repeat(64);
      return writeFixture(
        path.dirname(fixture.snapshotPath),
        fixture.snapshot,
        fixture.receipt,
        fixture.dbQcReceipt,
        { keepQcHash: true },
      );
    }, /snapshot QC receipt SHA-256 mismatch/],
    ["receipt card ID", async (fixture) => {
      fixture.receipt.cards[0].id = "cmc_ffffffffffffffffffffffff";
      return writeFixture(path.dirname(fixture.snapshotPath), fixture.snapshot, fixture.receipt, fixture.dbQcReceipt);
    }, /QC receipt card binding is invalid/],
    ["receipt snapshot facts", async (fixture) => {
      fixture.snapshot.top100[0].pricePsa10.value = 101;
      fixture.snapshot.top100[0].marketCap.value = 101000;
      fixture.snapshot.generation.contentSha256 = snapshotHash(fixture.snapshot);
      await writeFile(fixture.snapshotPath, `${JSON.stringify(fixture.snapshot, null, 2)}\n`);
      return fixture;
    }, /QC receipt contract is invalid/],
    ["snapshot content hash", async (fixture) => {
      const rewritten = await writeFixture(
        path.dirname(fixture.snapshotPath),
        fixture.snapshot,
        fixture.receipt,
        fixture.dbQcReceipt,
      );
      rewritten.snapshot.top100[0].pricePsa10.value = 101;
      await writeFile(rewritten.snapshotPath, `${JSON.stringify(rewritten.snapshot)}\n`);
      return rewritten;
    }, /generation content hash is inconsistent/],
  ];

  for (const [name, mutate, pattern] of cases) {
    await context.test(name, async () => {
      const directory = await mkdtemp(path.join(tmpdir(), `cardz-publish-fail-${name}-`));
      const fixture = await mutate(await productionFixture(path.join(directory, "input")));
      const out = path.join(directory, "out");
      await mkdir(out);
      const sentinel = '{"generationId":"last-good"}\n';
      await writeFile(path.join(out, "latest.json"), sentinel);
      await assert.rejects(publish(fixture, out), pattern);
      assert.equal(await readFile(path.join(out, "latest.json"), "utf8"), sentinel);
    });
  }

  await context.test("missing derivative", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "cardz-publish-fail-media-"));
    const fixture = await productionFixture(path.join(directory, "input"));
    const assets = path.join(directory, "assets");
    await mkdir(assets);
    const hash = fixture.snapshot.top100[0].image.sha256;
    await writeFile(
      path.join(assets, `${hash}.webp`),
      await readFile(path.join(root, "data/public/market-assets", `${hash}.webp`)),
    );
    const out = path.join(directory, "out");
    await mkdir(out);
    const sentinel = '{"generationId":"last-good"}\n';
    await writeFile(path.join(out, "latest.json"), sentinel);
    await assert.rejects(publish(fixture, out, ["--assets-root", assets]), /ENOENT|no such file/i);
    assert.equal(await readFile(path.join(out, "latest.json"), "utf8"), sentinel);
  });

  await context.test("swapped valid derivative", async () => {
    const directory = await mkdtemp(path.join(tmpdir(), "cardz-publish-fail-swapped-media-"));
    const fixture = await productionFixture(path.join(directory, "input"));
    const assets = path.join(directory, "assets");
    await mkdir(assets);
    const hash = fixture.snapshot.top100[0].image.sha256;
    const sourceAssets = path.join(root, "data/public/market-assets");
    for (const suffix of [".webp", "_200.webp", "_600.webp"]) {
      await writeFile(
        path.join(assets, `${hash}${suffix}`),
        await readFile(path.join(sourceAssets, `${hash}${suffix}`)),
      );
    }
    const replacement = (await readdir(sourceAssets)).find(
      (name) => /^[a-f0-9]{64}_200\.webp$/.test(name) && name !== `${hash}_200.webp`,
    );
    assert.ok(replacement, "a second valid 200px WEBP fixture is required");
    await writeFile(
      path.join(assets, `${hash}_200.webp`),
      await readFile(path.join(sourceAssets, replacement)),
    );
    const out = path.join(directory, "out");
    await mkdir(out);
    const sentinel = '{"generationId":"last-good"}\n';
    await writeFile(path.join(out, "latest.json"), sentinel);
    await assert.rejects(
      publish(fixture, out, ["--assets-root", assets]),
      /QC receipt media binding is invalid/,
    );
    assert.equal(await readFile(path.join(out, "latest.json"), "utf8"), sentinel);
  });
});
