import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execute = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

test("daily resolver uses 1d close-to-close and never self-anchors", async () => {
  const { stdout } = await execute("python", ["pipelines/daily_prices.py", "--self-test"], { cwd: root });
  const result = JSON.parse(stdout.trim());
  assert.equal(result.ready.status, "ready");
  assert.equal(result.ready.value, 10);
  assert.equal(result.accumulating.status, "accumulating");
  assert.equal(result.accumulating.value, null);
  assert.equal(result.singleClose.status, "accumulating");
  assert.equal(result.singleClose.value, null);
  assert.deepEqual(result.windowKeys, ["change_1d_pct", "change_7d_pct", "change_30d_pct"]);
  assert.equal(result.gap.status, "unavailable");
  assert.equal(result.earlierTie.anchorAt, "2026-07-20T23:00:00Z");
});

test("GemRate and population fallback resolvers accept exact identity only", async () => {
  const gemrate = JSON.parse((await execute("python", ["pipelines/gemrate_client.py", "--self-test"], { cwd: root })).stdout.trim());
  const population = JSON.parse((await execute("python", ["pipelines/population_daily.py"], { cwd: root })).stdout.trim());
  assert.equal(gemrate.rows, 1);
  assert.equal(gemrate.psa10, 1234);
  assert.equal(population.official.value, 1300);
  assert.equal(population.fallback.value, 1200);
});

test("immutable landing replays full input and archives only changed incremental files", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "cardz-g10-landing-"));
  const source = path.join(directory, "source");
  const landing = path.join(directory, "landing");
  const constituents = path.join(source, "index", "ptcg", "constituents.json");
  await mkdir(path.dirname(constituents), { recursive: true });
  await writeFile(constituents, JSON.stringify({ rows: [{ name: "Pikachu #001", priceUsd: 10, url: "https://private.invalid/card/source/card-1" }] }));

  const fullArgs = [
    "pipelines/g10_ingest.py", "--source-root", source, "--landing-root", landing,
    "--mode", "full", "--effective-at", "2026-07-21T00:00:00Z", "--archive-payload",
  ];
  const first = JSON.parse((await execute("python", fullArgs, { cwd: root })).stdout.trim());
  const replay = JSON.parse((await execute("python", fullArgs, { cwd: root })).stdout.trim());
  assert.equal(first.cardCount, 1);
  assert.equal(replay.replayed, true);

  await writeFile(constituents, JSON.stringify({ rows: [{ name: "Pikachu #001", priceUsd: 11, url: "https://private.invalid/card/source/card-1" }] }));
  const incremental = JSON.parse((await execute("python", [
    "pipelines/g10_ingest.py", "--source-root", source, "--landing-root", landing,
    "--mode", "incremental", "--effective-at", "2026-07-22T00:00:00Z", "--archive-payload",
  ], { cwd: root })).stdout.trim());
  const manifest = JSON.parse(await readFile(incremental.manifest, "utf8"));
  assert.equal(manifest.parentRunId, first.runId);
  assert.equal(manifest.changedFileCount, 1);
  assert.equal(manifest.deletedFileCount, 0);
});

test("daily orchestrator is fail-closed and contains no writable SQLite authority", async () => {
  await assert.rejects(
    execute("python", ["pipelines/run_daily.py", "--mode", "staging", "--local-only", "--source-root", path.join(root, "does-not-exist")], { cwd: root }),
    /source data root does not exist/i,
  );
  const help = (await execute("python", ["pipelines/run_daily.py", "--help"], { cwd: root })).stdout;
  assert.match(help, /--skip-market-source-refresh/);
  assert.match(help, /--refresh-bootstrap-source/);
  assert.match(help, /--refresh-active-universe/);
  assert.doesNotMatch(help, /--skip-source-refresh/);
  assert.doesNotMatch(help, /JLP|production-runner|mysql-dsn/i);
  const runner = await readFile(path.join(root, "pipelines/run_daily.py"), "utf8");
  assert.doesNotMatch(runner, /sqlite3|canonical-history\.sqlite|grade10_analytics|grade10_kline/i);
  assert.match(runner, /grade10_scraper\.py/);
  assert.match(runner, /source_crosswalk\.py/);
  assert.match(runner, /tracked_universe\.py/);
  assert.match(runner, /tracked-universe\.json/);
  assert.match(runner, /tracked-gemrate-ids\.txt/);
  assert.match(runner, /tracked-snk-ids\.txt/);
  assert.match(runner, /def bootstrap_history_exists[\s\S]*rglob\("canonical-batch\.json"\)/);
  assert.match(runner, /bootstrap_exists = bootstrap_history_exists\(landing_root\)/);
  assert.match(runner, /gemrate_source\.py/);
  assert.match(runner, /tag_daily_capture\.py/);
  assert.match(runner, /snk_market_data\.py/);
  assert.match(runner, /market_source_sync\.py/);
  assert.match(runner, /if tag_input is not None:[\s\S]*"--tag-run", str\(tag_input\)/);
  assert.match(runner, /tag_status = "unavailable"[\s\S]*tag_input = None/);
  assert.doesNotMatch(runner, /CARDZ_JLP|run_production_authority|production-runner|mysql-dsn/i);
  assert.doesNotMatch(runner, /--skip-images/);
  assert.match(runner, /observation_effective_at=price_effective_at/);
  assert.match(runner, /write_canonical_batch\(manifest_path, source_root, price_effective_at, landing_manifest\)/);
  assert.match(runner, /collect_or_reuse_fx/);
  assert.match(runner, /fx_snapshot=fx_snapshot/);
  assert.match(runner, /"--fx-snapshot", str\(fx_cache\)/);
  assert.match(runner, /CARDZ_GENERATION_CANARY_COMMAND_JSON/);
  assert.match(runner, /CARDZ_POINTER_PROMOTE_COMMAND_JSON/);
});

test("scheduler defaults to 06:30 unattended, singleton, and two-hour timeout", async (context) => {
  const installer = await readFile(path.join(root, "pipelines/install_daily_task.ps1"), "utf8");
  const scheduledRunner = await readFile(path.join(root, "pipelines/run_daily.ps1"), "utf8");
  assert.match(installer, /CARDZ-Market-Cap-Daily-Staging/);
  assert.match(installer, /Get-Command python\.exe[^\n]+-All[^\n]+Select-Object -First 1/);
  assert.match(installer, /06:30/);
  assert.match(installer, /LogonType S4U/);
  assert.match(installer, /MultipleInstances IgnoreNew/);
  assert.match(installer, /New-TimeSpan -Hours 2/);
  assert.match(installer, /RefreshBootstrapSource/);
  assert.match(installer, /grade10_scraper\.py/);
  assert.match(installer, /-PythonExe/);
  assert.match(installer, /-CanaryOrigin/);
  assert.match(installer, /-GenerationCanaryCommandJson/);
  assert.match(installer, /-PointerPromoteCommandJson/);
  assert.match(installer, /promote-staging-pointer\.mjs/);
  assert.doesNotMatch(installer, /run_daily\.bat|LogonType\s+Interactive/);
  assert.match(scheduledRunner, /\$env:CARDZ_DEPLOYMENT_ENV\s*=\s*\$Mode/);
  assert.match(scheduledRunner, /\$env:CARDZ_CANARY_ORIGIN\s*=/);
  assert.match(scheduledRunner, /\$env:CARDZ_GENERATION_CANARY_COMMAND_JSON\s*=/);
  assert.match(scheduledRunner, /\$env:CARDZ_POINTER_PROMOTE_COMMAND_JSON\s*=/);
  assert.match(scheduledRunner, /\$env:CARDZ_STAGING_R2_BUCKET\s*=\s*\$R2Bucket/);
  assert.match(scheduledRunner, /\[switch\]\$RefreshActiveUniverse/);
  assert.match(scheduledRunner, /\$Arguments \+= '--refresh-active-universe'/);
  assert.doesNotMatch(scheduledRunner, /CARDZ_JLP|ProductionRunner/);

  if (process.platform !== "win32") {
    context.diagnostic("Task Scheduler WhatIf execution is Windows-only; structural assertions passed.");
    return;
  }

  const python = (await execute("where.exe", ["python"])).stdout.trim().split(/\r?\n/)[0];
  const sourceScript = path.resolve(root, "../grade10-scraper/grade10_scraper.py");
  const preview = await execute("powershell.exe", [
    "-NoLogo", "-NoProfile", "-NonInteractive", "-File", path.join(root, "pipelines/install_daily_task.ps1"),
    "-PythonExe", python,
    "-R2Bucket", "cardz-test-private-bucket",
    "-CanaryOrigin", "https://cardz-canary.example.test",
    "-WhatIf",
  ], { cwd: root });
  assert.match(preview.stdout, /CARDZ_ACTION_ARGUMENTS=.*-PythonExe/);
  assert.doesNotMatch(preview.stdout, /PrivateAcquireScript|RefreshBootstrapSource/);
  assert.match(preview.stdout, new RegExp(python.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"));
  assert.match(preview.stdout, /-CanaryOrigin 'https:\/\/cardz-canary\.example\.test'/);
  assert.match(preview.stdout, /-GenerationCanaryCommandJson '\[.*run-generation-canary\.mjs.*\]'/);
  assert.match(preview.stdout, /-PointerPromoteCommandJson '\[.*promote-staging-pointer\.mjs.*\]'/);
});

test("exact source crosswalk and source normalizer are deterministic", async () => {
  const crosswalk = JSON.parse((await execute("python", ["pipelines/source_crosswalk.py", "--self-test"], { cwd: root })).stdout.trim());
  assert.equal(crosswalk.counts.cards, 2);
  assert.equal(crosswalk.counts.gemrateExact, 2);
  assert.equal(crosswalk.counts.snkExact, 1);
  assert.equal(crosswalk.hashStable, true);

  const universe = JSON.parse((await execute("python", ["pipelines/active_universe.py", "--self-test"], { cwd: root })).stdout.trim());
  assert.equal(universe.ja.active, 300);
  assert.equal(universe.ja.top100, 100);
  assert.equal(universe.ja.watchlist, 200);
  assert.equal(universe.maxSegment, 300);
  assert.equal(universe.allOver100, true);
  assert.equal(universe.koreanAlias, "ko");
  assert.equal(universe.thaiAlias, "");
  assert.equal(universe.nativeKoreanAccepted, true);
  assert.equal(universe.englishOnlyKoreanRejected, true);
  assert.equal(universe.lockCreated, true);
  assert.equal(universe.lockReused, true);
  assert.equal(universe.providerIdsFromLock, true);
  assert.match(universe.lockId, /^universe_20260723T000000Z_[a-f0-9]{12}$/);

  const activeUniverseSource = await readFile(path.join(root, "pipelines/active_universe.py"), "utf8");
  assert.match(activeUniverseSource, /if output\.is_file\(\) and not args\.refresh_lock:/);
  assert.match(activeUniverseSource, /load_active_universe_lock\(output, lock_root\)/);
  assert.match(activeUniverseSource, /write_provider_ids\(document, gemrate_ids, snk_ids\)/);
  assert.match(activeUniverseSource, /with path\.open\("xb"\)/);
  assert.match(activeUniverseSource, /immutable active-universe locks exist but the active pointer is missing or legacy/);

  const sourceSync = JSON.parse((await execute("python", ["pipelines/market_source_sync.py", "--self-test"], { cwd: root })).stdout.trim());
  assert.equal(sourceSync.priceUsd, 100);
  assert.equal(sourceSync.priority, 200);
  assert.equal(sourceSync.counts.snkPriceObservations, 1);
  assert.equal(sourceSync.tagTopGradePopulation, 12);
  assert.equal(sourceSync.tagTotal, 21);
  assert.equal(sourceSync.tagPriority, 150);
  assert.equal(sourceSync.counts.tagObservations, 1);

  const tag = JSON.parse((await execute("python", ["pipelines/tag_daily_capture.py", "--self-test"], { cwd: root })).stdout.trim());
  assert.equal(tag.counts.matched, 1);
  assert.equal(tag.matchedTopGrade, 14);
  assert.equal(tag.matchedTotalExcludesVa, 14);
  assert.equal(tag.suffixWasNotGuessed, true);
  assert.equal(tag.ambiguousWasQuarantined, true);
  assert.equal(tag.secondReplay, true);
});

test("GemRate, TAG, and SNK collectors keep credentials private and publish only complete runs", async () => {
  const gemrate = await readFile(path.join(root, "pipelines/gemrate_source.py"), "utf8");
  const tag = await readFile(path.join(root, "pipelines/tag_daily_capture.py"), "utf8");
  const tagClient = await readFile(path.join(root, "pipelines/tag_pop_data.py"), "utf8");
  const snkClient = await readFile(path.join(root, "pipelines/snkrdunk_bulk.py"), "utf8");
  const snkPipeline = await readFile(path.join(root, "pipelines/snk_market_data.py"), "utf8");
  assert.doesNotMatch(gemrate, /subprocess\.run\(|f["']x-api-key:/);
  assert.match(gemrate, /os\.environ\.get\("GEMRATE_API_KEY"/);
  assert.match(gemrate, /os\.replace\(temporary, path\)/);
  assert.match(tag, /ambiguous_exact_identity/);
  assert.match(tag, /no_exact_identity/);
  assert.match(tag, /write_immutable_jsonl/);
  assert.doesNotMatch(tag, /match_600|join_card/);
  assert.match(tagClient, /dump_fresh/);
  assert.match(tagClient, /partial\.with_suffix\(partial\.suffix \+ "\.state"\)/);
  assert.match(tagClient, /os\.replace\(complete, destination\)/);
  assert.match(snkClient, /response\.status_code == 429/);
  assert.match(snkClient, /500 <= response\.status_code < 600/);
  assert.match(snkPipeline, /default="trading_card_single_psa10"/);
  assert.match(snkPipeline, /os\.replace\(partial, out_path\)/);
  assert.match(snkPipeline, /state_path\.unlink\(missing_ok=True\)/);
});

test("publisher verifies all raw-front assets before writing latest pointer", async () => {
  const directory = await mkdtemp(path.join(tmpdir(), "cardz-publish-"));
  const { stdout } = await execute(
    "node",
    ["pipelines/publish-snapshot.mjs", "--allow-demo", "--out", directory],
    { cwd: root },
  );
  const result = JSON.parse(stdout.trim());
  const pointer = JSON.parse(await readFile(path.join(directory, "latest.json"), "utf8"));
  const generation = JSON.parse(await readFile(path.join(directory, ...result.generationKey.split("/")), "utf8"));
  assert.equal(pointer.generationId, generation.generation.id);
  assert.equal(pointer.sha256, generation.generation.contentSha256);
  assert.ok(result.assetCount >= 100);
  assert.equal(result.remoteAssetsVerified, 0);
});

test("publisher refuses demo data without explicit allow-demo", async () => {
  await assert.rejects(
    execute("node", ["pipelines/publish-snapshot.mjs", "--out", await mkdtemp(path.join(tmpdir(), "cardz-blocked-"))], { cwd: root }),
    /generation mode is not production|generation is release blocked/,
  );
});

test("remote publisher requires generation canary and conditional pointer promoter before any R2 write", async () => {
  await assert.rejects(
    execute("node", [
      "pipelines/publish-snapshot.mjs", "--allow-demo", "--r2-bucket", "never-contact-this-bucket",
      "--out", await mkdtemp(path.join(tmpdir(), "cardz-remote-blocked-")),
    ], { cwd: root }),
    /requires a generation-scoped canary command/,
  );
  const publisher = await readFile(path.join(root, "pipelines/publish-snapshot.mjs"), "utf8");
  assert.doesNotMatch(publisher, /r2Put\([^\n]+["']latest\.json["']/);
  assert.match(publisher, /CARDZ_EXPECTED_LATEST_SHA256/);
  assert.match(publisher, /receipt\.conditional !== true/);
});

test("publisher fails before pointer when a referenced raw-front is missing or changed", async () => {
  const value = JSON.parse(await readFile(path.join(root, "data/public/seed-snapshot.json"), "utf8"));
  const firstAsset = path.basename(value.top100[0].image.src);

  const missingRoot = await mkdtemp(path.join(tmpdir(), "cardz-assets-missing-"));
  await assert.rejects(
    execute("node", [
      "pipelines/publish-snapshot.mjs", "--allow-demo", "--assets-root", missingRoot,
      "--out", await mkdtemp(path.join(tmpdir(), "cardz-assets-missing-out-")),
    ], { cwd: root }),
    /ENOENT|no such file/i,
  );

  const changedRoot = await mkdtemp(path.join(tmpdir(), "cardz-assets-changed-"));
  await writeFile(path.join(changedRoot, firstAsset), "changed-image-bytes");
  await assert.rejects(
    execute("node", [
      "pipelines/publish-snapshot.mjs", "--allow-demo", "--assets-root", changedRoot,
      "--out", await mkdtemp(path.join(tmpdir(), "cardz-assets-changed-out-")),
    ], { cwd: root }),
    /local raw-front hash mismatch/i,
  );
});
