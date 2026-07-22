import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFile } from "node:child_process";
import { mkdir, mkdtemp, readFile, stat, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";

const executeFile = promisify(execFile);
const root = path.resolve(import.meta.dirname, "..");

function sha256(contents) {
  return createHash("sha256").update(contents).digest("hex");
}

function sortObject(value) {
  if (Array.isArray(value)) return value.map(sortObject);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, sortObject(value[key])]));
  }
  return value;
}

function canonicalSnapshotHash(snapshot) {
  const clone = structuredClone(snapshot);
  clone.generation.contentSha256 = "";
  return sha256(JSON.stringify(sortObject(clone)));
}

async function exists(file) {
  return stat(file).then(() => true, () => false);
}

async function runHook(script, environment) {
  return executeFile(process.execPath, [script], {
    cwd: root,
    env: { ...process.env, ...environment },
  });
}

async function makeFakeCommands(directory) {
  const canary = path.join(directory, "fake-canary.mjs");
  const wrangler = path.join(directory, "fake-wrangler.mjs");
  await writeFile(canary, `
const args = process.argv.slice(2);
const origin = args[args.indexOf("--origin") + 1];
const generation = args[args.indexOf("--expect-generation") + 1];
if (origin !== process.env.FAKE_EXPECT_ORIGIN || generation !== process.env.FAKE_EXPECT_GENERATION) process.exit(2);
if (process.env.FAKE_CANARY_FAIL === "1") process.exit(3);
`);
  await writeFile(wrangler, `
import { copyFile, mkdir } from "node:fs/promises";
import path from "node:path";
const args = process.argv.slice(2);
if (args[0] !== "r2" || args[1] !== "object" || !["get", "put"].includes(args[2])) process.exit(2);
if (process.env.FAKE_WRANGLER_FAIL === "auth") { console.error("authentication failed"); process.exit(4); }
const [bucket, ...keyParts] = args[3].split("/");
const key = keyParts.join("/");
const file = args[args.indexOf("--file") + 1];
const object = path.join(process.env.FAKE_R2_ROOT, bucket, ...key.split("/"));
if (args[2] === "get") await copyFile(object, file);
else { await mkdir(path.dirname(object), { recursive: true }); await copyFile(file, object); }
`);
  return { canary, wrangler };
}

test("generation canary removes stale receipts and writes one only after the exact generation passes", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "cardz-canary-hook-"));
  const { canary } = await makeFakeCommands(directory);
  const receipt = path.join(directory, "canary-receipt.json");
  await writeFile(receipt, JSON.stringify({ passed: true, generationId: "stale" }));
  const environment = {
    NODE_ENV: "test",
    CARDZ_HOOK_TEST_MODE: "1",
    CARDZ_PUBLIC_CANARY_COMMAND_JSON: JSON.stringify([process.execPath, canary]),
    CARDZ_CANARY_ORIGIN: "https://canary.example.test",
    CARDZ_CANDIDATE_GENERATION_ID: "daily_20260722",
    CARDZ_CANARY_RECEIPT_PATH: receipt,
    FAKE_EXPECT_ORIGIN: "https://canary.example.test",
    FAKE_EXPECT_GENERATION: "daily_20260722",
    FAKE_CANARY_FAIL: "1",
  };
  await assert.rejects(runHook("pipelines/run-generation-canary.mjs", environment));
  assert.equal(await exists(receipt), false);

  delete environment.FAKE_CANARY_FAIL;
  await runHook("pipelines/run-generation-canary.mjs", environment);
  assert.deepEqual(JSON.parse(await readFile(receipt, "utf8")), {
    schemaVersion: 1,
    passed: true,
    generationId: "daily_20260722",
  });
});

test("staging promoter verifies baseline, candidate pointer, generation and readback before receipt", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "cardz-promoter-hook-"));
  const { wrangler } = await makeFakeCommands(directory);
  const bucket = "cardz-market-cap-staging-test";
  const objectRoot = path.join(directory, "r2", bucket);
  const generationId = "daily_20260722";
  const generationKey = `generations/${generationId}/snapshot.json`;
  const snapshot = { schemaVersion: 2, generation: { id: generationId, contentSha256: "" }, top100: [], watchlist: [] };
  snapshot.generation.contentSha256 = canonicalSnapshotHash(snapshot);
  const candidate = {
    schemaVersion: 1,
    generationId,
    snapshotKey: generationKey,
    sha256: snapshot.generation.contentSha256,
  };
  const baseline = { schemaVersion: 1, generationId: "prior", snapshotKey: "generations/prior/snapshot.json", sha256: "a".repeat(64) };
  const candidatePayload = `${JSON.stringify(candidate, null, 2)}\n`;
  const baselinePayload = `${JSON.stringify(baseline, null, 2)}\n`;
  await mkdir(path.join(objectRoot, "generations", generationId), { recursive: true });
  await writeFile(path.join(objectRoot, ...generationKey.split("/")), `${JSON.stringify(snapshot, null, 2)}\n`);
  await writeFile(path.join(objectRoot, "candidate.json"), candidatePayload);
  await writeFile(path.join(objectRoot, "latest.json"), baselinePayload);

  const request = {
    schemaVersion: 1,
    bucket,
    expectedLatestSha256: sha256(baselinePayload),
    candidatePointerKey: "candidate.json",
    candidatePointerSha256: sha256(candidatePayload),
    generationId,
    generationKey,
  };
  const requestPath = path.join(directory, "promotion-request.json");
  const receipt = path.join(directory, "promotion-receipt.json");
  await writeFile(requestPath, JSON.stringify(request));
  const environment = {
    NODE_ENV: "test",
    CARDZ_HOOK_TEST_MODE: "1",
    CARDZ_WRANGLER_COMMAND_JSON: JSON.stringify([process.execPath, wrangler]),
    FAKE_R2_ROOT: path.join(directory, "r2"),
    CARDZ_DEPLOYMENT_ENV: "staging",
    CARDZ_R2_BUCKET: bucket,
    CARDZ_STAGING_R2_BUCKET: bucket,
    CARDZ_POINTER_PROMOTION_REQUEST: requestPath,
    CARDZ_POINTER_PROMOTION_RECEIPT: receipt,
  };
  await runHook("pipelines/promote-staging-pointer.mjs", environment);
  const result = JSON.parse(await readFile(receipt, "utf8"));
  assert.equal(result.promoted, true);
  assert.equal(result.conditional, true);
  assert.equal(result.conditionalScope, "staging-singleton-baseline-check");
  assert.deepEqual(JSON.parse(await readFile(path.join(objectRoot, "latest.json"), "utf8")), candidate);

  await writeFile(receipt, JSON.stringify({ promoted: true, generationId: "stale" }));
  await assert.rejects(runHook("pipelines/promote-staging-pointer.mjs", environment), /latest\.json changed/i);
  assert.equal(await exists(receipt), false);
  assert.deepEqual(JSON.parse(await readFile(path.join(objectRoot, "latest.json"), "utf8")), candidate);
});

test("bundled promoter refuses production environments and buckets without mutating the pointer", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "cardz-promoter-prod-"));
  const { wrangler } = await makeFakeCommands(directory);
  const requestPath = path.join(directory, "request.json");
  const receipt = path.join(directory, "receipt.json");
  await writeFile(requestPath, JSON.stringify({ generationId: "candidate" }));
  await assert.rejects(runHook("pipelines/promote-staging-pointer.mjs", {
    NODE_ENV: "test",
    CARDZ_HOOK_TEST_MODE: "1",
    CARDZ_WRANGLER_COMMAND_JSON: JSON.stringify([process.execPath, wrangler]),
    FAKE_R2_ROOT: path.join(directory, "r2"),
    CARDZ_DEPLOYMENT_ENV: "production",
    CARDZ_R2_BUCKET: "cardz-market-cap-production",
    CARDZ_STAGING_R2_BUCKET: "cardz-market-cap-production",
    CARDZ_POINTER_PROMOTION_REQUEST: requestPath,
    CARDZ_POINTER_PROMOTION_RECEIPT: receipt,
  }), /staging-only/i);
  assert.equal(await exists(receipt), false);
});

test("staging promoter does not mistake an R2 failure for an absent baseline", async () => {
  const directory = await mkdtemp(path.join(os.tmpdir(), "cardz-promoter-r2-fail-"));
  const { wrangler } = await makeFakeCommands(directory);
  const requestPath = path.join(directory, "request.json");
  const receipt = path.join(directory, "receipt.json");
  await writeFile(requestPath, JSON.stringify({
    bucket: "cardz-staging-test",
    expectedLatestSha256: "absent",
    candidatePointerKey: "candidate.json",
    candidatePointerSha256: "a".repeat(64),
    generationId: "candidate",
    generationKey: "generations/candidate/snapshot.json",
  }));
  await assert.rejects(runHook("pipelines/promote-staging-pointer.mjs", {
    NODE_ENV: "test",
    CARDZ_HOOK_TEST_MODE: "1",
    CARDZ_WRANGLER_COMMAND_JSON: JSON.stringify([process.execPath, wrangler]),
    FAKE_WRANGLER_FAIL: "auth",
    FAKE_R2_ROOT: path.join(directory, "r2"),
    CARDZ_DEPLOYMENT_ENV: "staging",
    CARDZ_R2_BUCKET: "cardz-staging-test",
    CARDZ_STAGING_R2_BUCKET: "cardz-staging-test",
    CARDZ_POINTER_PROMOTION_REQUEST: requestPath,
    CARDZ_POINTER_PROMOTION_RECEIPT: receipt,
  }), /R2 operation failed/i);
  assert.equal(await exists(receipt), false);
});
