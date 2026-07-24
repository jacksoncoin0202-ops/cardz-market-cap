import { createHash } from "node:crypto";
import { execFile, execFileSync } from "node:child_process";
import { copyFile, mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

import { assertPublicSnapshot } from "../packages/market-data/dist/index.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const executeFile = promisify(execFile);

function parseArgs(argv) {
  const options = {
    snapshot: path.join(root, "data", "public", "seed-snapshot.json"),
    assetsRoot: path.join(root, "data", "public", "market-assets"),
    imageManifest: path.join(root, "manifests", "image-qc.json"),
    out: path.join(root, "data", "public", "publish-staging"),
    allowDemo: false,
    r2Bucket: null,
    canaryCommand: process.env.CARDZ_GENERATION_CANARY_COMMAND_JSON ?? null,
    promoteCommand: process.env.CARDZ_POINTER_PROMOTE_COMMAND_JSON ?? null,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--snapshot") options.snapshot = path.resolve(argv[++index]);
    else if (value === "--assets-root") options.assetsRoot = path.resolve(argv[++index]);
    else if (value === "--image-manifest") options.imageManifest = path.resolve(argv[++index]);
    else if (value === "--out") options.out = path.resolve(argv[++index]);
    else if (value === "--allow-demo") options.allowDemo = true;
    else if (value === "--r2-bucket") options.r2Bucket = argv[++index];
    else if (value === "--canary-command-json") options.canaryCommand = argv[++index];
    else if (value === "--promote-command-json") options.promoteCommand = argv[++index];
    else throw new Error(`Unknown argument: ${value}`);
  }
  return options;
}

function canonicalHash(snapshot) {
  const clone = structuredClone(snapshot);
  clone.generation.contentSha256 = "";
  const stable = JSON.stringify(sortObject(clone));
  return createHash("sha256").update(stable).digest("hex");
}

function sortObject(value) {
  if (Array.isArray(value)) return value.map(sortObject);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, sortObject(value[key])]),
    );
  }
  return value;
}

function wranglerCommand() {
  return [process.execPath, path.join(root, "node_modules", "wrangler", "bin", "wrangler.js")];
}

async function atomicWrite(destination, contents) {
  await mkdir(path.dirname(destination), { recursive: true });
  const temporary = `${destination}.${process.pid}.tmp`;
  await writeFile(temporary, contents);
  await rename(temporary, destination);
}

async function runWrangler(args, { optional = false } = {}) {
  const command = wranglerCommand();
  try {
    await executeFile(command[0], [...command.slice(1), ...args], {
      cwd: root,
      env: process.env,
      maxBuffer: 4 * 1024 * 1024,
    });
    return true;
  } catch (error) {
    const diagnostic = [error?.stderr, error?.stdout, error?.message]
      .filter(Boolean)
      .map((value) => Buffer.isBuffer(value) ? value.toString("utf8") : String(value))
      .join("\n");
    if (optional && /(?:no such key|not found|404|does not exist|enoent)/i.test(diagnostic)) return false;
    throw new Error("R2 operation failed");
  }
}

async function r2Put(bucket, key, file, contentType = "application/json", cacheControl = null) {
  const args = ["r2", "object", "put", `${bucket}/${key}`, "--file", file, "--remote", "--content-type", contentType];
  if (cacheControl) args.push("--cache-control", cacheControl);
  await runWrangler(args);
}

function sha256(contents) {
  return createHash("sha256").update(contents).digest("hex");
}

async function collectAssets(snapshot, assetsRoot) {
  const assets = new Map();
  for (const card of [...snapshot.top100, ...snapshot.watchlist]) {
    const match = card.image.src.match(/^\/market-assets\/([a-f0-9]{64})\.(webp)$/);
    if (!match) throw new Error(`invalid public raw-front path for ${card.id}`);
    const [, hash, extension] = match;
    if (hash !== card.image.sha256) throw new Error(`image path/hash mismatch for ${card.id}`);
    const filename = `${hash}.${extension}`;
    const file = path.join(assetsRoot, filename);
    const contents = await readFile(file);
    if (sha256(contents) !== hash) throw new Error(`local raw-front hash mismatch: ${filename}`);
    assets.set(filename, { file, hash, key: `market-assets/${filename}`, contentType: "image/webp" });
    /* 縮圖 derivative：optional，有就一齊上傳（唔 hash-verify，內容由 master derive） */
    for (const suffix of ["200", "600"]) {
      const derivativeFile = path.join(assetsRoot, `${hash}_${suffix}.webp`);
      try {
        await readFile(derivativeFile);
        assets.set(`${hash}_${suffix}.webp`, { file: derivativeFile, hash, key: `market-assets/${hash}_${suffix}.webp`, contentType: "image/webp", skipHashVerify: true });
      } catch { /* derivative 唔存在就 skip */ }
    }
  }
  return [...assets.values()].sort((left, right) => left.key.localeCompare(right.key));
}

async function validateImageQc(snapshot, imageManifest, strictSemantic) {
  const manifest = JSON.parse(await readFile(imageManifest, "utf8"));
  const records = new Map(
    (manifest.records ?? [])
      .filter((record) => record?.publicAllowed)
      .map((record) => [record.contentSha256, record]),
  );
  for (const card of [...snapshot.top100, ...snapshot.watchlist]) {
    const record = records.get(card.image.sha256);
    if (!record) throw new Error(`public raw-front has no public-allowed QC record: ${card.id}`);
    if (strictSemantic && record.semanticMatchStatus !== "human_or_vision_confirmed") {
      throw new Error(`public raw-front is not human_or_vision_confirmed: ${card.id}`);
    }
  }
}

async function r2Get(bucket, key, file) {
  await runWrangler(["r2", "object", "get", `${bucket}/${key}`, "--file", file, "--remote"]);
}

function parseCommand(raw, label) {
  if (!raw) throw new Error(`remote publish requires ${label}`);
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

function runHook(command, environment) {
  execFileSync(command[0], command.slice(1), {
    cwd: root,
    env: { ...process.env, ...environment },
    stdio: "inherit",
  });
}

async function readJsonRequired(file, label) {
  try {
    return JSON.parse(await readFile(file, "utf8"));
  } catch {
    throw new Error(`${label} did not emit valid JSON: ${file}`);
  }
}

async function r2GetOptional(bucket, key, file) {
  return runWrangler(["r2", "object", "get", `${bucket}/${key}`, "--file", file, "--remote"], { optional: true });
}

async function forEachConcurrent(items, concurrency, worker) {
  let nextIndex = 0;
  const runners = Array.from({ length: Math.min(concurrency, items.length) }, async () => {
    while (nextIndex < items.length) {
      const item = items[nextIndex];
      nextIndex += 1;
      await worker(item);
    }
  });
  await Promise.all(runners);
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const snapshot = JSON.parse(await readFile(options.snapshot, "utf8"));
  assertPublicSnapshot(snapshot, { production: !options.allowDemo });
  const assets = await collectAssets(snapshot, options.assetsRoot);
  await validateImageQc(snapshot, options.imageManifest, !options.allowDemo);
  const expectedHash = canonicalHash(snapshot);
  if (expectedHash !== snapshot.generation.contentSha256) {
    throw new Error("snapshot contentSha256 does not match canonical content");
  }
  const canaryCommand = options.r2Bucket ? parseCommand(options.canaryCommand, "a generation-scoped canary command") : null;
  const promoteCommand = options.r2Bucket ? parseCommand(options.promoteCommand, "a conditional pointer promotion command") : null;

  const generationKey = `generations/${snapshot.generation.id}/snapshot.json`;
  const remoteScope = options.r2Bucket
    ? createHash("sha256").update(options.r2Bucket).digest("hex").slice(0, 16)
    : null;
  const pointerPath = path.join(options.out, "latest.json");
  const pointer = {
    schemaVersion: 1,
    generationId: snapshot.generation.id,
    generatedAt: snapshot.generation.generatedAt,
    snapshotKey: generationKey,
    sha256: snapshot.generation.contentSha256,
    media: {
      prefix: "market-assets/",
      hashes: assets.map((asset) => asset.hash),
      remoteVerified: Boolean(options.r2Bucket),
      remoteScope,
    },
  };
  const generationPath = path.join(options.out, ...generationKey.split("/"));
  const snapshotPayload = `${JSON.stringify(snapshot, null, 2)}\n`;
  const pointerPayload = `${JSON.stringify(pointer, null, 2)}\n`;

  await atomicWrite(generationPath, snapshotPayload);
  const localAssetsRoot = path.join(options.out, "generations", snapshot.generation.id, "assets");
  await mkdir(localAssetsRoot, { recursive: true });
  for (const asset of assets) await copyFile(asset.file, path.join(localAssetsRoot, path.basename(asset.file)));
  const persisted = JSON.parse(await readFile(generationPath, "utf8"));
  if (canonicalHash(persisted) !== pointer.sha256) throw new Error("persisted generation verification failed");

  if (options.r2Bucket) {
    const verifyRoot = path.join(options.out, "remote-assets.verify");
    await mkdir(verifyRoot, { recursive: true });
    // Content-addressing makes uploads idempotent, but a local pointer is not
    // evidence that another bucket still contains the bytes. Verify every
    // referenced remote object before the generation can be promoted.
    let verifiedAssetCount = 0;
    await forEachConcurrent(assets, 6, async (asset) => {
      await r2Put(options.r2Bucket, asset.key, asset.file, asset.contentType, "public,max-age=31536000,immutable");
      if (asset.skipHashVerify) {
        verifiedAssetCount += 1;
        return;
      }
      const remoteAsset = path.join(verifyRoot, path.basename(asset.file));
      await r2Get(options.r2Bucket, asset.key, remoteAsset);
      if (sha256(await readFile(remoteAsset)) !== asset.hash) {
        throw new Error(`remote raw-front verification failed: ${asset.key}; latest.json was not advanced`);
      }
      verifiedAssetCount += 1;
      if (verifiedAssetCount % 50 === 0 || verifiedAssetCount === assets.length) {
        process.stdout.write(`Verified ${verifiedAssetCount}/${assets.length} remote raw-front assets.\n`);
      }
    });
    await r2Put(options.r2Bucket, generationKey, generationPath);
    const remoteGenerationTemp = path.join(options.out, "remote-generation.verify.json");
    await r2Get(options.r2Bucket, generationKey, remoteGenerationTemp);
    const remoteGeneration = JSON.parse(await readFile(remoteGenerationTemp, "utf8"));
    if (
      remoteGeneration.generation?.id !== snapshot.generation.id ||
      canonicalHash(remoteGeneration) !== pointer.sha256
    ) {
      throw new Error("remote generation verification failed; latest.json was not advanced");
    }

    // The persistent canary Worker is bound to the same staging bucket but
    // reads candidate.json instead of latest.json.
    const candidatePointerKey = "candidate.json";
    const candidatePointerFile = path.join(options.out, "candidate-pointer.json");
    await atomicWrite(candidatePointerFile, pointerPayload);
    await r2Put(options.r2Bucket, candidatePointerKey, candidatePointerFile);
    const candidatePointerVerify = path.join(options.out, "remote-candidate-pointer.verify.json");
    await r2Get(options.r2Bucket, candidatePointerKey, candidatePointerVerify);
    if (sha256(await readFile(candidatePointerVerify)) !== sha256(Buffer.from(pointerPayload))) {
      throw new Error("remote candidate pointer verification failed; latest.json was not advanced");
    }

    const baselinePointerFile = path.join(options.out, "remote-latest.baseline.json");
    const baselineExists = await r2GetOptional(options.r2Bucket, "latest.json", baselinePointerFile);
    const baselinePointerSha256 = baselineExists ? sha256(await readFile(baselinePointerFile)) : "absent";
    const canaryReceipt = path.join(options.out, "generation-canary-receipt.json");
    const promotionRequest = path.join(options.out, "pointer-promotion-request.json");
    const promotionReceipt = path.join(options.out, "pointer-promotion-receipt.json");
    await atomicWrite(promotionRequest, `${JSON.stringify({
      schemaVersion: 1,
      bucket: options.r2Bucket,
      expectedLatestSha256: baselinePointerSha256,
      candidatePointerKey,
      candidatePointerSha256: sha256(Buffer.from(pointerPayload)),
      generationId: snapshot.generation.id,
      generationKey,
    }, null, 2)}\n`);
    const hookEnvironment = {
      CARDZ_R2_BUCKET: options.r2Bucket,
      CARDZ_CANDIDATE_POINTER_KEY: candidatePointerKey,
      CARDZ_CANDIDATE_GENERATION_ID: snapshot.generation.id,
      CARDZ_CANDIDATE_GENERATION_KEY: generationKey,
      CARDZ_EXPECTED_LATEST_SHA256: baselinePointerSha256,
      CARDZ_CANARY_RECEIPT_PATH: canaryReceipt,
      CARDZ_POINTER_PROMOTION_REQUEST: promotionRequest,
      CARDZ_POINTER_PROMOTION_RECEIPT: promotionReceipt,
    };
    runHook(canaryCommand, hookEnvironment);
    const canary = await readJsonRequired(canaryReceipt, "generation canary");
    if (canary.passed !== true || canary.generationId !== snapshot.generation.id) {
      throw new Error("generation-scoped canary did not approve the candidate; latest.json was not advanced");
    }
    runHook(promoteCommand, hookEnvironment);
    const receipt = await readJsonRequired(promotionReceipt, "conditional pointer promoter");
    if (
      receipt.promoted !== true ||
      receipt.conditional !== true ||
      receipt.generationId !== snapshot.generation.id ||
      receipt.expectedLatestSha256 !== baselinePointerSha256
    ) {
      throw new Error("conditional pointer promoter receipt is invalid");
    }
    const remotePointerVerify = path.join(options.out, "remote-latest.verify.json");
    await r2Get(options.r2Bucket, "latest.json", remotePointerVerify);
    const persistedPointer = JSON.parse(await readFile(remotePointerVerify, "utf8"));
    if (
      persistedPointer.generationId !== pointer.generationId ||
      persistedPointer.snapshotKey !== pointer.snapshotKey ||
      persistedPointer.sha256 !== pointer.sha256
    ) {
      throw new Error("remote latest.json verification failed");
    }
  }
  await atomicWrite(pointerPath, pointerPayload);
  process.stdout.write(
    `${JSON.stringify({
      generationKey,
      pointer: pointerPath,
      mode: snapshot.generation.mode,
      remotePublished: Boolean(options.r2Bucket),
      remoteGenerationVerified: Boolean(options.r2Bucket),
      remotePointerVerified: Boolean(options.r2Bucket),
      assetCount: assets.length,
      remoteAssetsVerified: options.r2Bucket ? assets.length : 0,
      reusedAssetCount: 0,
      uploadedAssetCount: options.r2Bucket ? assets.length : 0,
    })}\n`,
  );
}

main().catch((error) => {
  process.stderr.write(`${error.message}\n`);
  process.exitCode = 1;
});
