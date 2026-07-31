import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile, stat } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execute = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
// Ubuntu 24.04 冇 /usr/bin/python，Windows 嘅 python3 又係 Store 假 alias，所以兩邊各用各嘅名。
const PYTHON = process.env.CARDZ_PYTHON ?? (process.platform === "win32" ? "python" : "python3");

test("G10 full and incremental ingestion is immutable, idempotent, and keeps sale semantics", async () => {
  const { stdout } = await execute(PYTHON, ["pipelines/g10_ingest.py", "--self-test"], { cwd: root });
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
  assert.deepEqual(result.graders, ["PSA", "BGS", "CGC", "SGC", "TAG"]);
  assert.equal(result.landingReplay.priceAnchorCount, 3);
  assert.equal(result.landingReplay.populationAnchorCount, 3);
  assert.equal(result.landingReplay.populationTotalRetained, true);
  assert.equal(result.landingReplay.identicalSameDateRetryIgnored, true);
  assert.equal(result.landingReplay.laterPriceCorrectionWins, true);
  assert.equal(result.landingReplay.laterPopulationCorrectionWins, true);
  assert.equal(result.landingReplay.twoDay1d.status, "ready");
  assert.equal(result.landingReplay.twoDay1d.value, 10);
  assert.equal(result.landingReplay.twoDay7d.status, "accumulating");
  assert.equal(result.landingReplay.eightDay7d.status, "ready");
  assert.equal(result.landingReplay.eightDay7d.value, 21);
  assert.equal(result.landingReplay.populationEightDay7d.status, "ready");
  assert.equal(result.landingReplay.populationEightDay7d.value, 20);
});

test("standalone migration is additive and MySQL 5.7 compatible", async () => {
  const sql = await readFile(path.join(root, "pipelines/migrations/002_market_observations.mysql.sql"), "utf8");
  const fxSql = await readFile(path.join(root, "pipelines/migrations/003_fx_rate_observations.mysql.sql"), "utf8");
  const provenanceSql = await readFile(
    path.join(root, "pipelines/migrations/007_canonical_provenance.mysql.sql"),
    "utf8",
  );
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
  assert.match(
    sql,
    /UNIQUE KEY uq_market_source_observation\s*\(source_code, external_entity_id, observation_kind, observed_date, payload_sha256\)/,
  );
  assert.match(sql, /UNIQUE KEY uq_market_grader_population \(variant_id, grader_code, source_code, observed_date\)/);
  assert.doesNotMatch(sql, /PRAGMA|AUTOINCREMENT|INSERT OR IGNORE|ON CONFLICT/i);
  assert.match(fxSql, /CREATE TABLE IF NOT EXISTS market_fx_rate_observation\b/);
  assert.match(fxSql, /UNIQUE KEY uq_market_fx_rate_daily \(base_currency, quote_currency, effective_date\)/);
  assert.doesNotMatch(fxSql, /PRAGMA|AUTOINCREMENT|INSERT OR IGNORE|ON CONFLICT/i);
  for (const table of [
    "catalog_printing_identity",
    "market_population_transport_observation",
    "catalog_story_pointer",
    "market_image_source_pointer",
  ]) {
    assert.match(provenanceSql, new RegExp(`CREATE TABLE IF NOT EXISTS ${table}\\b`));
  }
  assert.match(
    provenanceSql,
    /UNIQUE KEY uq_population_transport_daily\s*\(variant_id, authority_code, transport_code, grader_code, grade_label, effective_date\)/,
  );
  assert.doesNotMatch(provenanceSql, /PRAGMA|AUTOINCREMENT|INSERT OR IGNORE|ON CONFLICT/i);
});

test("canonical replay materializes detail and transport observations", async () => {
  const runtime = await readFile(path.join(root, "pipelines/db_runtime.py"), "utf8");
  for (const kind of [
    "identity_candidate",
    "story_pointer",
    "image_metadata",
    "sale_observation_psa10",
    "tracked_sales_1d",
    "tracked_sales_7d",
    "tracked_sales_30d",
  ]) {
    assert.match(runtime, new RegExp(`"${kind}"`));
  }
  for (const table of [
    "catalog_printing_identity",
    "market_population_transport_observation",
    "catalog_story_pointer",
    "market_image_source_pointer",
    "market_sale_observation",
    "market_tracked_sales_aggregate",
  ]) {
    assert.match(runtime, new RegExp(`INSERT(?: IGNORE)? INTO ${table}\\b`));
  }
});

test("daily FX adapter validates all supported currencies including JPY and KRW", async () => {
  const { stdout } = await execute(PYTHON, ["pipelines/fx_rates.py", "--self-test"], { cwd: root });
  const result = JSON.parse(stdout.trim());
  assert.deepEqual(result.supported, ["USD", "HKD", "CNY", "GBP", "TWD", "JPY", "KRW"]);
  assert.equal(result.krw.status, "ready");
  assert.equal(result.jpy.value, 162.5);
  assert.equal(result.jpy.status, "ready");
  assert.equal(result.state, null);
  assert.equal(result.hashStable, true);
  assert.equal(result.lastGoodReused, true);
  assert.equal(result.expiredLastGoodBlocked, true);
});

test("shared runtime validator does not embed private provider vocabulary", async () => {
  const runtime = await readFile(path.join(root, "packages/market-data/dist/validate.js"), "utf8");
  assert.doesNotMatch(runtime, /grade10|gemrate|snkrdunk|sneakerdunk|ebay/i);
});

test("G10 public builder emits exact Top 100 with no private source vocabulary", async (context) => {
  const privateSourceRoot = path.resolve(root, "../grade10-scraper/data");
  const privateKadoRoot = path.resolve(root, "../kado-dump");
  const privateSourceAvailable = await stat(privateSourceRoot).then(() => true, () => false);
  const privateKadoAvailable = await stat(privateKadoRoot).then(() => true, () => false);
  if (!privateSourceAvailable || !privateKadoAvailable) {
    context.skip("private read-only G10 source is intentionally absent from clean CI clones");
    return;
  }
  const { stdout } = await execute(
    PYTHON,
    [
      "pipelines/g10_public_snapshot.py",
      "--self-test",
      "--source-root", privateSourceRoot,
      "--kado-root", privateKadoRoot,
    ],
    { cwd: root },
  );
  const result = JSON.parse(stdout.trim());
  assert.equal(result.top100, 100);
  assert.ok(result.minimumPopulation >= 1000);
  assert.equal(result.incompleteCollectorNumbers, 0);
  assert.equal(result.nonRawImages, 0);
  assert.equal(result.privateTokens, 0);
  assert.equal(result.japanesePromoLanguageMismatches, 0);
  assert.equal(result.englishSvpCollector, "085/SVP");
  assert.ok(Array.isArray(result.englishMepCollectors));
  assert.ok(result.englishMepCollectors.every((value) => /^\d{3}\/MEP$/.test(value)));
  assert.ok(Object.values(result.collectorQc).every((value) => typeof value === "string" && value.length > 2));
  const isCompletePromoNumber = (value) => (
    typeof value === "string" && (
      value.includes("/")
      || /^(?:OP|ST|EB)\d{2}-\d{3}$/i.test(value)
      || /^P-\d{3}$/i.test(value)
      || /^(?:SM|SWSH)\d+$/i.test(value)
    )
  );
  assert.ok(Object.values(result.promoCollectorQc).every(isCompletePromoNumber));
  assert.equal(result.publicDuplicateVariantImageGroups, 0);
  assert.ok(Number.isInteger(result.quarantinedDuplicateVariantImageGroups));
  assert.ok(result.quarantinedDuplicateVariantImageGroups >= 0);
});

test("current private universe yields an exact 600-card source crosswalk", async (context) => {
  const privateSourceRoot = path.resolve(root, "../grade10-scraper/data");
  const privateSourceAvailable = await stat(privateSourceRoot).then(() => true, () => false);
  if (!privateSourceAvailable) {
    context.skip("private read-only G10 source is intentionally absent from clean CI clones");
    return;
  }
  const directory = path.resolve(root, "data/runtime/test-source-crosswalk");
  const { stdout } = await execute(PYTHON, [
    "pipelines/source_crosswalk.py",
    "--source-root", privateSourceRoot,
    "--out", path.join(directory, "crosswalk.json"),
    "--gemrate-ids-out", path.join(directory, "gemrate.txt"),
    "--snk-ids-out", path.join(directory, "snk.txt"),
  ], { cwd: root });
  const result = JSON.parse(stdout.trim());
  assert.equal(result.cards, 600);
  assert.equal(result.gemrateExact, 600);
  // Floor 而唔係等號：cards / gemrateExact / pokemon / onePiece 係 frozen 600 卡 universe
  // 嘅邊界常數，但 snkExact 係「600 卡入面夾到 SNK item id」嘅覆蓋率，crosswalk 每次夾多
  // 幾張就會升（寫呢個測試嗰陣 442，今日 479）。寫死等號等於每次覆蓋率進步都假紅，所以只
  // 守歷史低位，真係跌穿先算 regression。
  assert.ok(result.snkExact >= 442, `snkExact 跌穿已知低位 442：${result.snkExact}`);
  assert.equal(result.pokemon, 500);
  assert.equal(result.onePiece, 100);

  const active = JSON.parse((await execute(PYTHON, [
    "pipelines/active_universe.py",
    "--source-root", privateSourceRoot,
    "--crosswalk", path.join(directory, "crosswalk.json"),
    "--out", path.join(directory, "active.json"),
    "--gemrate-ids-out", path.join(directory, "active-gemrate.txt"),
    "--snk-ids-out", path.join(directory, "active-snk.txt"),
  ], { cwd: root })).stdout.trim());
  assert.ok(active.active > 100);
  assert.ok(Object.values(active.segments).every((segment) => segment.active <= 300));
  assert.equal(Object.keys(active.segments).some((key) => key.endsWith(":th")), false);
  assert.ok(["created", "replayed", "reused"].includes(active.lockStatus));
  const activeDocument = JSON.parse(await readFile(path.join(directory, "active.json"), "utf8"));
  assert.match(activeDocument.lock.lockId, /^universe_/);
  assert.equal(activeDocument.lock.selectionPolicy, "top100_plus_up_to_200_per_tcg_x_card_language");
  assert.deepEqual(activeDocument.policy.supportedCardLanguages, ["en", "ja", "ko", "zhCN", "zhTW"]);
  assert.deepEqual(activeDocument.policy.excludedCardLanguages, ["th"]);
  assert.equal(activeDocument.policy.koreanNativeTextRequired, true);

  const reused = JSON.parse((await execute(PYTHON, [
    "pipelines/active_universe.py",
    "--source-root", path.join(directory, "source-does-not-exist"),
    "--out", path.join(directory, "active.json"),
    "--gemrate-ids-out", path.join(directory, "active-gemrate.txt"),
    "--snk-ids-out", path.join(directory, "active-snk.txt"),
  ], { cwd: root })).stdout.trim());
  assert.equal(reused.lockStatus, "reused");
  assert.equal(reused.lockId, activeDocument.lock.lockId);
  assert.ok((await readFile(path.join(directory, "active-gemrate.txt"), "utf8")).trim().length > 0);
});
