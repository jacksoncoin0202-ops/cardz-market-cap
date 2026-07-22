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
    execute("python", ["pipelines/run_daily.py", "--mode", "staging", "--local-only"], { cwd: root }),
    /private acquisition is not configured/i,
  );
  await assert.rejects(
    execute("python", ["pipelines/run_daily.py", "--mode", "production", "--skip-source-refresh", "--local-only"], { cwd: root }),
    /production requires the JLP MySQL runner and remote R2 publish/i,
  );
  const runner = await readFile(path.join(root, "pipelines/run_daily.py"), "utf8");
  assert.doesNotMatch(runner, /sqlite3|canonical-history\.sqlite|grade10_analytics|grade10_kline/i);
  assert.match(runner, /grade10_scraper\.py/);
  assert.doesNotMatch(runner, /--skip-images/);
  assert.match(runner, /observation_effective_at=price_effective_at/);
  assert.match(runner, /write_canonical_batch\(manifest_path, source_root, price_effective_at, landing_manifest\)/);
  assert.match(runner, /CARDZ_GENERATION_CANARY_COMMAND_JSON/);
  assert.match(runner, /CARDZ_POINTER_PROMOTE_COMMAND_JSON/);
});

test("scheduler defaults to 06:30 unattended, singleton, and two-hour timeout", async () => {
  const installer = await readFile(path.join(root, "pipelines/install_daily_task.ps1"), "utf8");
  const scheduledRunner = await readFile(path.join(root, "pipelines/run_daily.ps1"), "utf8");
  assert.match(installer, /Grade10-Daily-Scraper/);
  assert.match(installer, /06:30/);
  assert.match(installer, /LogonType S4U/);
  assert.match(installer, /MultipleInstances IgnoreNew/);
  assert.match(installer, /New-TimeSpan -Hours 2/);
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

  const python = (await execute("where.exe", ["python"])).stdout.trim().split(/\r?\n/)[0];
  const sourceScript = path.resolve(root, "../grade10-scraper/grade10_scraper.py");
  const preview = await execute("powershell.exe", [
    "-NoLogo", "-NoProfile", "-NonInteractive", "-File", path.join(root, "pipelines/install_daily_task.ps1"),
    "-PrivateAcquireScript", sourceScript,
    "-PythonExe", python,
    "-R2Bucket", "cardz-test-private-bucket",
    "-CanaryOrigin", "https://cardz-canary.example.test",
    "-WhatIf",
  ], { cwd: root });
  assert.match(preview.stdout, /CARDZ_ACTION_ARGUMENTS=.*-PythonExe/);
  assert.match(preview.stdout, new RegExp(python.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "i"));
  assert.match(preview.stdout, /-CanaryOrigin 'https:\/\/cardz-canary\.example\.test'/);
  assert.match(preview.stdout, /-GenerationCanaryCommandJson '\[.*run-generation-canary\.mjs.*\]'/);
  assert.match(preview.stdout, /-PointerPromoteCommandJson '\[.*promote-staging-pointer\.mjs.*\]'/);
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
