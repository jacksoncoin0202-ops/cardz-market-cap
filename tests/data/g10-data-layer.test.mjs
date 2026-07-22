import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execute = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("G10 full and incremental ingestion is immutable, idempotent, and keeps sale semantics", async () => {
  const { stdout } = await execute("python", ["pipelines/g10_ingest.py", "--self-test"], { cwd: root });
  const result = JSON.parse(stdout.trim());
  assert.equal(result.full.accepted, 2);
  assert.equal(result.full.rejected, 1);
  assert.equal(result.replay.inserted, 0);
  assert.equal(result.replay.replayed, true);
  assert.equal(result.incremental.inserted, 1);
  assert.equal(result.sales.count, 3);
  assert.equal(result.sales.valueUsd, 400);
  assert.equal(result.sales.bundleUnitPrice, 100);
  assert.equal(result.sales.samePriceTransactions, 2);
  assert.equal(result.sales.sameSaleStableAcrossFetches, true);
  assert.equal(result.sales.sameDayDuplicatesRemainDistinct, true);
  assert.equal(result.sales.ambiguousBundleQuarantined, true);
  assert.equal(result.observationIdentity.sameDayKeyStable, true);
  assert.equal(result.observationIdentity.nextDayKeyChanged, true);
  assert.equal(result.observationIdentity.fullTimestampRetained, true);
  assert.equal(result.observationIdentity.fixedPriceDateAcrossFetches, true);
  assert.equal(result.observationIdentity.separateFetchTimeRetained, true);
  assert.equal(result.observationIdentity.populationUsesFetchDate, true);
  assert.equal(result.observationIdentity.populationValueRetained, true);
  assert.equal(result.windows["1d"].status, "ready");
  assert.equal(result.windows["7d"].status, "accumulating");
  assert.equal(result.windows["30d"].status, "accumulating");
  assert.equal(result.singleCloseWindows["1d"].status, "accumulating");
  assert.equal(result.singleCloseWindows["1d"].value, null);
  assert.deepEqual(result.graders, ["PSA", "BGS", "CGC", "SGC"]);
  assert.equal(result.landingReplay.priceAnchorCount, 3);
  assert.equal(result.landingReplay.populationAnchorCount, 3);
  assert.equal(result.landingReplay.sameDateRetryIgnored, true);
  assert.equal(result.landingReplay.firstPriceWins, true);
  assert.equal(result.landingReplay.firstPopulationWins, true);
  assert.equal(result.landingReplay.twoDay1d.status, "ready");
  assert.equal(result.landingReplay.twoDay1d.value, 10);
  assert.equal(result.landingReplay.twoDay7d.status, "accumulating");
  assert.equal(result.landingReplay.eightDay7d.status, "ready");
  assert.equal(result.landingReplay.eightDay7d.value, 21);
  assert.equal(result.landingReplay.populationEightDay7d.status, "ready");
  assert.equal(result.landingReplay.populationEightDay7d.value, 20);
});

test("JLP extension migration is additive and MySQL 5.7 compatible", async () => {
  const sql = await readFile(path.join(root, "pipelines/migrations/002_market_observations.mysql.sql"), "utf8");
  for (const table of [
    "market_ingest_run",
    "market_ingest_checkpoint",
    "market_source_observation",
    "market_index_snapshot",
    "market_index_constituent",
    "market_sale_observation",
    "market_tracked_sales_aggregate",
    "market_image_asset",
    "market_image_qc",
  ]) {
    assert.match(sql, new RegExp(`CREATE TABLE IF NOT EXISTS ${table}\\b`));
  }
  assert.match(sql, /FOREIGN KEY \(variant_id\) REFERENCES catalog_variant\(id\)/);
  assert.match(sql, /UNIQUE KEY uq_market_source_observation \(source_code, external_entity_id, observation_kind, observed_date\)/);
  assert.match(sql, /UNIQUE KEY uq_market_grader_population \(variant_id, grader_code, source_code, observed_date\)/);
  assert.doesNotMatch(sql, /PRAGMA|AUTOINCREMENT|INSERT OR IGNORE|ON CONFLICT/i);
});

test("shared runtime validator does not embed private provider vocabulary", async () => {
  const runtime = await readFile(path.join(root, "packages/market-data/dist/validate.js"), "utf8");
  assert.doesNotMatch(runtime, /grade10|gemrate|snkrdunk|sneakerdunk|ebay/i);
});

test("G10 public builder emits exact Top 100 with no private source vocabulary", async (context) => {
  const privateSourceRoot = path.resolve(root, "../grade10-scraper/data");
  const privateSourceAvailable = await stat(privateSourceRoot).then(() => true, () => false);
  if (!privateSourceAvailable) {
    context.skip("private read-only G10 source is intentionally absent from clean CI clones");
    return;
  }
  const { stdout } = await execute("python", ["pipelines/g10_public_snapshot.py", "--self-test"], { cwd: root });
  const result = JSON.parse(stdout.trim());
  assert.equal(result.top100, 100);
  assert.ok(result.minimumPopulation >= 1000);
  assert.equal(result.incompleteCollectorNumbers, 0);
  assert.equal(result.nonRawImages, 0);
  assert.equal(result.privateTokens, 0);
  assert.equal(result.japanesePromoLanguageMismatches, 0);
  assert.equal(result.englishSvpCollector, "085/SVP");
  assert.ok(result.englishMepCollectors.includes("023/MEP"));
  assert.ok(result.englishMepCollectors.includes("024/MEP"));
  assert.deepEqual(result.collectorQc, {
    30: "78/73",
    32: "001/025",
    43: "020/019",
    45: "007/025",
    48: "083/067",
    51: "080/073",
    52: "GG69/GG70",
    53: "001/030",
    55: "GG44/GG70",
    60: "094/087",
    62: "SV49/SV94",
    72: "010/032",
    83: "082/072",
    88: "SV107/SV122",
    94: "012/025",
  });
  assert.deepEqual(result.promoCollectorQc, {
    1: "085/SVP",
    3: "227/S-P",
    4: "294/XY-P",
    7: "288/SM-P",
    9: "207/XY-P",
    19: "296/XY-P",
    21: "276/XY-P",
    27: "289/SM-P",
    36: "293/XY-P",
    37: "270/SM-P",
    38: "286/SM-P",
    44: "090/XY-P",
    63: "295/XY-P",
    70: "208/S-P",
    77: "400/SM-P",
    100: "053/SVP",
  });
  assert.equal(result.publicDuplicateVariantImageGroups, 0);
  assert.ok(result.quarantinedDuplicateVariantImageGroups >= 1);
});
