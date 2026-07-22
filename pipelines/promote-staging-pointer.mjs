#!/usr/bin/env node

import { createHash } from "node:crypto";
import { execFileSync } from "node:child_process";
import { mkdir, mkdtemp, readFile, rename, rm, unlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SAFE_GENERATION = /^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$/;
const SHA256 = /^[a-f0-9]{64}$/;

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
  if (!clone.generation || typeof clone.generation !== "object") throw new Error("candidate generation is invalid");
  clone.generation.contentSha256 = "";
  return sha256(JSON.stringify(sortObject(clone)));
}

function parseCommand(raw, label) {
  let command;
  try {
    command = JSON.parse(raw);
  } catch {
    throw new Error(`${label} must be a JSON array`);
  }
  if (!Array.isArray(command) || command.length === 0 || command.some((part) => typeof part !== "string" || !part)) {
    throw new Error(`${label} must be a non-empty JSON string array`);
  }
  return command;
}

function wranglerCommand() {
  if (process.env.CARDZ_HOOK_TEST_MODE === "1" && process.env.NODE_ENV === "test") {
    return parseCommand(process.env.CARDZ_WRANGLER_COMMAND_JSON ?? "", "CARDZ_WRANGLER_COMMAND_JSON");
  }
  return [process.execPath, path.join(root, "node_modules", "wrangler", "bin", "wrangler.js")];
}

function runWrangler(args, { optional = false } = {}) {
  const command = wranglerCommand();
  try {
    execFileSync(command[0], [...command.slice(1), ...args], {
      cwd: root,
      env: process.env,
      stdio: "pipe",
    });
    return true;
  } catch (error) {
    const diagnostic = [error?.stderr, error?.stdout, error?.message]
      .filter(Boolean)
      .map((value) => Buffer.isBuffer(value) ? value.toString("utf8") : String(value))
      .join("\n");
    if (optional && /(?:no such key|not found|404|does not exist|enoent)/i.test(diagnostic)) return false;
    throw new Error("staging R2 operation failed");
  }
}

function r2Get(bucket, key, destination, { optional = false } = {}) {
  return runWrangler(["r2", "object", "get", `${bucket}/${key}`, "--file", destination, "--remote"], { optional });
}

function r2Put(bucket, key, source) {
  runWrangler(["r2", "object", "put", `${bucket}/${key}`, "--file", source, "--remote", "--content-type", "application/json"]);
}

async function atomicWrite(destination, contents) {
  await mkdir(path.dirname(destination), { recursive: true });
  const temporary = `${destination}.${process.pid}.tmp`;
  await writeFile(temporary, contents);
  await rename(temporary, destination);
}

async function readJson(file, label) {
  try {
    return JSON.parse(await readFile(file, "utf8"));
  } catch {
    throw new Error(`${label} is not valid JSON`);
  }
}

function requireEnvironment(name) {
  const value = process.env[name]?.trim() ?? "";
  if (!value) throw new Error(`${name} is required`);
  return value;
}

async function main() {
  const rawReceiptPath = process.env.CARDZ_POINTER_PROMOTION_RECEIPT?.trim() ?? "";
  if (rawReceiptPath) {
    await unlink(path.resolve(rawReceiptPath)).catch((error) => {
      if (error.code !== "ENOENT") throw error;
    });
  }
  const environment = requireEnvironment("CARDZ_DEPLOYMENT_ENV");
  const bucket = requireEnvironment("CARDZ_R2_BUCKET");
  const stagingBucket = requireEnvironment("CARDZ_STAGING_R2_BUCKET");
  const requestPath = path.resolve(requireEnvironment("CARDZ_POINTER_PROMOTION_REQUEST"));
  const receiptPath = path.resolve(requireEnvironment("CARDZ_POINTER_PROMOTION_RECEIPT"));

  if (environment !== "staging") throw new Error("the bundled pointer promoter is staging-only");
  if (bucket !== stagingBucket || /(?:^|[-_.])prod(?:uction)?(?:$|[-_.])/i.test(bucket)) {
    throw new Error("the bundled pointer promoter refuses non-staging buckets");
  }

  const request = await readJson(requestPath, "pointer promotion request");
  const generationId = request.generationId;
  const expectedGenerationKey = `generations/${generationId}/snapshot.json`;
  if (!SAFE_GENERATION.test(generationId ?? "")) throw new Error("promotion request generation ID is invalid");
  if (request.bucket !== bucket) throw new Error("promotion request bucket does not match the staging allowlist");
  if (request.candidatePointerKey !== "candidate.json") throw new Error("promotion request candidate key is invalid");
  if (request.generationKey !== expectedGenerationKey) throw new Error("promotion request generation key is invalid");
  if (request.expectedLatestSha256 !== "absent" && !SHA256.test(request.expectedLatestSha256 ?? "")) {
    throw new Error("promotion request baseline SHA-256 is invalid");
  }
  if (!SHA256.test(request.candidatePointerSha256 ?? "")) {
    throw new Error("promotion request candidate SHA-256 is invalid");
  }

  const work = await mkdtemp(path.join(os.tmpdir(), "cardz-pointer-"));
  const baselineFile = path.join(work, "latest.baseline.json");
  const candidateFile = path.join(work, "candidate.json");
  const generationFile = path.join(work, "snapshot.json");
  const verifyBaselineFile = path.join(work, "latest.pre-put.json");
  const verifyLatestFile = path.join(work, "latest.verify.json");

  try {
    const baselineExists = r2Get(bucket, "latest.json", baselineFile, { optional: true });
    const baselineHash = baselineExists ? sha256(await readFile(baselineFile)) : "absent";
    if (baselineHash !== request.expectedLatestSha256) throw new Error("latest.json changed after the publisher captured its baseline");

    r2Get(bucket, request.candidatePointerKey, candidateFile);
    const candidateBytes = await readFile(candidateFile);
    if (sha256(candidateBytes) !== request.candidatePointerSha256) throw new Error("candidate pointer SHA-256 mismatch");
    const candidate = await readJson(candidateFile, "candidate pointer");
    if (
      candidate.generationId !== generationId ||
      candidate.snapshotKey !== expectedGenerationKey ||
      !SHA256.test(candidate.sha256 ?? "")
    ) {
      throw new Error("candidate pointer does not match the requested generation");
    }

    r2Get(bucket, expectedGenerationKey, generationFile);
    const snapshot = await readJson(generationFile, "candidate generation");
    if (snapshot.generation?.id !== generationId || canonicalSnapshotHash(snapshot) !== candidate.sha256) {
      throw new Error("candidate generation verification failed");
    }

    // Wrangler has no If-Match support. Re-check immediately before the put.
    // The outer daily singleton is the staging serialization boundary.
    const secondBaselineExists = r2Get(bucket, "latest.json", verifyBaselineFile, { optional: true });
    const secondBaselineHash = secondBaselineExists ? sha256(await readFile(verifyBaselineFile)) : "absent";
    if (secondBaselineHash !== request.expectedLatestSha256) throw new Error("latest.json changed before staging promotion");

    r2Put(bucket, "latest.json", candidateFile);
    r2Get(bucket, "latest.json", verifyLatestFile);
    const verifiedBytes = await readFile(verifyLatestFile);
    const verifiedPointer = await readJson(verifyLatestFile, "promoted pointer");
    if (
      sha256(verifiedBytes) !== request.candidatePointerSha256 ||
      verifiedPointer.generationId !== generationId ||
      verifiedPointer.snapshotKey !== expectedGenerationKey ||
      verifiedPointer.sha256 !== candidate.sha256
    ) {
      throw new Error("staging latest.json readback verification failed");
    }

    await atomicWrite(receiptPath, `${JSON.stringify({
      schemaVersion: 1,
      promoted: true,
      conditional: true,
      conditionalScope: "staging-singleton-baseline-check",
      generationId,
      expectedLatestSha256: request.expectedLatestSha256,
      candidatePointerSha256: request.candidatePointerSha256,
    }, null, 2)}\n`);
  } finally {
    await rm(work, { recursive: true, force: true });
  }
}

main().catch((error) => {
  process.stderr.write(`staging pointer promotion failed: ${error.message}\n`);
  process.exitCode = 1;
});
