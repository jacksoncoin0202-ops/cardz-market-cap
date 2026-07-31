import { createHash } from "node:crypto";
import { execFile, execFileSync } from "node:child_process";
import { mkdir, readFile, rename, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

import { assertPublicSnapshot } from "../packages/market-data/dist/index.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const executeFile = promisify(execFile);
const routingConfigPath = path.join(root, "config", "data-routing.json");

function parseArgs(argv) {
  const options = {
    snapshot: path.join(root, "data", "public", "seed-snapshot.json"),
    assetsRoot: path.join(root, "data", "public", "market-assets"),
    qcReceipt: null,
    dbQcReceipt: null,
    out: path.join(root, "data", "public", "publish-staging"),
    r2Bucket: null,
    canaryCommand: process.env.CARDZ_GENERATION_CANARY_COMMAND_JSON ?? null,
    promoteCommand: process.env.CARDZ_POINTER_PROMOTE_COMMAND_JSON ?? null,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--snapshot") options.snapshot = path.resolve(argv[++index]);
    else if (value === "--assets-root") options.assetsRoot = path.resolve(argv[++index]);
    else if (value === "--qc-receipt") options.qcReceipt = path.resolve(argv[++index]);
    else if (value === "--db-qc-receipt") options.dbQcReceipt = path.resolve(argv[++index]);
    else if (value === "--out") options.out = path.resolve(argv[++index]);
    else if (value === "--r2-bucket") options.r2Bucket = argv[++index];
    else if (value === "--canary-command-json") options.canaryCommand = argv[++index];
    else if (value === "--promote-command-json") options.promoteCommand = argv[++index];
    else throw new Error(`Unknown argument: ${value}`);
  }
  if (!options.qcReceipt) throw new Error("--qc-receipt is required");
  if (!options.dbQcReceipt) throw new Error("--db-qc-receipt is required");
  return options;
}

function canonicalHash(snapshot) {
  const clone = structuredClone(snapshot);
  clone.generation.contentSha256 = "";
  const stable = JSON.stringify(sortObject(clone));
  return createHash("sha256").update(stable).digest("hex");
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
  // Python's json loader preserves the configured 2.0 as a float while
  // JavaScript parses it as 2.  Keep the cross-runtime policy digest stable.
  if (field === "priceSpreadMaximumRatio" && typeof value === "number" && Number.isInteger(value)) {
    return value.toFixed(1);
  }
  return JSON.stringify(value);
}

function canonicalJsonBytes(value) {
  return Buffer.from(`${pythonCompatibleCanonicalJson(value)}\n`, "utf8");
}

function isSha256(value) {
  return typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
}

function isPositiveInteger(value) {
  return Number.isInteger(value) && value > 0;
}

function profilePolicySha256(profileId, policy) {
  return sha256(canonicalJsonBytes({ releaseProfile: profileId, policy }));
}

async function loadReleaseProfile(snapshot) {
  let routing;
  try {
    routing = JSON.parse(await readFile(routingConfigPath, "utf8"));
  } catch {
    throw new Error("release routing config is not valid JSON");
  }
  const profiles = routing?.releaseProfiles;
  if (!profiles || typeof profiles !== "object" || Array.isArray(profiles)) {
    throw new Error("release routing config has no release profiles");
  }
  const generation = snapshot?.generation;
  const declared = generation?.releaseProfile;
  // An absent profile retains the strict receipt predicate below.  Every
  // explicitly profiled generation is immutable-bound to the policy currently
  // named in the routing config.
  const id = declared === undefined ? "strict-v1" : declared;
  if (typeof id !== "string" || !Object.hasOwn(profiles, id)) {
    throw new Error("snapshot release profile is invalid");
  }
  const policy = profiles[id];
  if (!policy || typeof policy !== "object" || Array.isArray(policy)) {
    throw new Error("release profile policy is invalid");
  }
  const policySha256 = profilePolicySha256(id, policy);
  const bound = declared !== undefined;
  if (!bound) return { id, policy, policySha256, bound };

  if (generation.policySha256 !== policySha256) {
    throw new Error("snapshot release profile/hash does not match routing config");
  }
  if (!isSha256(generation.dbFingerprint) || !isPositiveInteger(generation.evaluationId)) {
    throw new Error("snapshot release profile database binding is invalid");
  }
  return { id, policy, policySha256, bound };
}

function releaseBinding(snapshot, release) {
  if (!release.bound) return {};
  return {
    releaseProfile: release.id,
    policySha256: release.policySha256,
    databaseFingerprint: snapshot.generation.dbFingerprint,
    evaluationId: snapshot.generation.evaluationId,
  };
}

function validateReleaseSnapshotContract(snapshot, release) {
  const policy = release.policy;
  const requiredCount = policy.requestedCount;
  const minimumCount = policy.minimumVerifiedCount;
  const cardMaximum = policy.publicCardsMaximum;
  const assetMaximum = policy.publicImageAssetsMaximum;
  const cards = [...snapshot.top100, ...snapshot.watchlist];
  if (
    !Number.isInteger(requiredCount)
    || !Number.isInteger(minimumCount)
    || !Number.isInteger(cardMaximum)
    || !Number.isInteger(assetMaximum)
    || requiredCount < 1
    || minimumCount < 1
    || cardMaximum < requiredCount
    || assetMaximum < 1
  ) {
    throw new Error("release profile capacities are invalid");
  }
  if (
    cards.length > cardMaximum
    || snapshot.watchlist.length > cardMaximum - requiredCount
  ) {
    throw new Error("snapshot exceeds release profile card capacity");
  }
  if (release.id === "relaxed-launch-v1") {
    if (snapshot.top100.length !== requiredCount || cards.length < minimumCount) {
      throw new Error("relaxed snapshot does not meet its release card minimum");
    }
  }
  const allowedIdentityStatuses = policy.allowedIdentityStatuses;
  if (!Array.isArray(allowedIdentityStatuses) || allowedIdentityStatuses.some((status) => typeof status !== "string")) {
    throw new Error("release profile identity statuses are invalid");
  }
  for (const card of cards) {
    if (!allowedIdentityStatuses.includes(card.identityStatus)) {
      throw new Error(`snapshot identity status is not allowed by ${release.id}: ${card.id}`);
    }
  }
  return { cards, requiredCount, cardMaximum, assetMaximum };
}

function receiptSnapshotContentHash(snapshot) {
  const clone = structuredClone(snapshot);
  clone.generation.contentSha256 = "";
  clone.generation.qcReceiptSha256 = "";
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

async function immutableWrite(destination, contents, label) {
  const payload = Buffer.isBuffer(contents) ? contents : Buffer.from(contents);
  try {
    const existing = await readFile(destination);
    if (!existing.equals(payload)) {
      throw new Error(`immutable ${label} mismatch: ${destination}`);
    }
    return;
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }
  await atomicWrite(destination, payload);
}

async function immutableCopy(source, destination, label) {
  await immutableWrite(destination, await readFile(source), label);
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

const MEDIA_SPECS = {
  base: { suffix: "", width: null, height: null },
  "200": { suffix: "_200", width: 200, height: 280 },
  "600": { suffix: "_600", width: 429, height: 600 },
};

function webpDimensions(contents, filename) {
  if (
    contents.length < 20
    || contents.subarray(0, 4).toString("ascii") !== "RIFF"
    || contents.subarray(8, 12).toString("ascii") !== "WEBP"
  ) {
    throw new Error(`local raw-front is not WEBP: ${filename}`);
  }
  let offset = 12;
  while (offset + 8 <= contents.length) {
    const chunk = contents.subarray(offset, offset + 4).toString("ascii");
    const size = contents.readUInt32LE(offset + 4);
    const data = offset + 8;
    const end = data + size;
    if (end > contents.length) break;
    if (chunk === "VP8X" && size >= 10) {
      return {
        width: 1 + contents.readUIntLE(data + 4, 3),
        height: 1 + contents.readUIntLE(data + 7, 3),
      };
    }
    if (
      chunk === "VP8 "
      && size >= 10
      && contents[data + 3] === 0x9d
      && contents[data + 4] === 0x01
      && contents[data + 5] === 0x2a
    ) {
      return {
        width: contents.readUInt16LE(data + 6) & 0x3fff,
        height: contents.readUInt16LE(data + 8) & 0x3fff,
      };
    }
    if (chunk === "VP8L" && size >= 5 && contents[data] === 0x2f) {
      return {
        width: 1 + contents[data + 1] + ((contents[data + 2] & 0x3f) << 8),
        height: 1
          + (contents[data + 2] >> 6)
          + (contents[data + 3] << 2)
          + ((contents[data + 4] & 0x0f) << 10),
      };
    }
    offset = end + (size % 2);
  }
  throw new Error(`local raw-front WEBP dimensions are invalid: ${filename}`);
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
    const baseDimensions = webpDimensions(contents, filename);
    if (
      card.image.width !== baseDimensions.width
      || card.image.height !== baseDimensions.height
    ) {
      throw new Error(`local raw-front base dimensions are invalid: ${filename}`);
    }
    assets.set(filename, {
      file,
      baseHash: hash,
      contentSha256: hash,
      key: `market-assets/${filename}`,
      contentType: "image/webp",
      width: baseDimensions.width,
      height: baseDimensions.height,
      variant: "base",
    });
    for (const suffix of ["200", "600"]) {
      const expectedPath = `/market-assets/${hash}_${suffix}.webp`;
      if (card.image.variants?.[suffix] !== expectedPath) {
        throw new Error(`required ${suffix}px raw-front path is missing or invalid for ${card.id}`);
      }
      const derivativeFile = path.join(assetsRoot, `${hash}_${suffix}.webp`);
      const derivativeContents = await readFile(derivativeFile);
      const derivativeName = `${hash}_${suffix}.webp`;
      const derivativeDimensions = webpDimensions(derivativeContents, derivativeName);
      if (
        derivativeDimensions.width !== MEDIA_SPECS[suffix].width
        || derivativeDimensions.height !== MEDIA_SPECS[suffix].height
      ) {
        throw new Error(`local raw-front ${suffix}px derivative dimensions are invalid: ${derivativeName}`);
      }
      assets.set(derivativeName, {
        file: derivativeFile,
        baseHash: hash,
        contentSha256: sha256(derivativeContents),
        key: `market-assets/${derivativeName}`,
        contentType: "image/webp",
        width: derivativeDimensions.width,
        height: derivativeDimensions.height,
        variant: suffix,
      });
    }
  }
  return [...assets.values()].sort((left, right) => left.key.localeCompare(right.key));
}

function sameJson(left, right) {
  return JSON.stringify(sortObject(left)) === JSON.stringify(sortObject(right));
}

function validateDbQcReceipt(snapshot, receipt, receiptBytes, release) {
  const counts = receipt?.counts;
  const releaseGate = receipt?.releaseGate;
  const binding = {
    runId: receipt?.runId,
    receiptSha256: sha256(receiptBytes),
    database: receipt?.database,
    universeCandidateSha256: receipt?.universeCandidateSha256,
  };
  const countFields = [
    "qualified",
    "monitoring",
    "releaseReadyQualified",
    "releaseBlockedQualified",
  ];
  if (
    receipt?.schemaVersion !== 1
    || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/.test(receipt.runId ?? "")
    || !Number.isFinite(Date.parse(receipt.asOf))
    || receipt.status !== "passed"
    || receipt.readOnly !== true
    || receipt.database !== "cardz_market_cap"
    || !/^[a-f0-9]{64}$/.test(receipt.universeCandidateSha256 ?? "")
    || !/^[a-f0-9]{64}$/.test(receipt.reportSha256 ?? "")
    || !counts
    || countFields.some((field) => !Number.isInteger(counts[field]) || counts[field] < 0)
    || !releaseGate
    || releaseGate.eligible !== true
  ) {
    throw new Error("canonical DB QC receipt contract is invalid");
  }
  if (!release.bound) {
    // Preserve the original strict receipt contract for historical snapshots.
    if (
      counts.releaseBlockedQualified !== 0
      || counts.releaseReadyQualified !== counts.qualified
      || releaseGate.blockerCount !== 0
      || releaseGate.blockerCardCount !== 0
      || !sameJson(releaseGate.blockers, {})
    ) {
      throw new Error("canonical DB QC receipt contract is invalid");
    }
  } else {
    const expected = releaseBinding(snapshot, release);
    const minimumEligible = release.policy.minimumVerifiedCount;
    if (
      receipt.releaseProfile !== expected.releaseProfile
      || receipt.policySha256 !== expected.policySha256
      || receipt.databaseFingerprint !== expected.databaseFingerprint
      || receipt.evaluationId !== expected.evaluationId
      || !Number.isInteger(counts.minimumReleaseEligible)
      || counts.minimumReleaseEligible !== minimumEligible
      || !Number.isInteger(releaseGate.minimumEligible)
      || releaseGate.minimumEligible !== minimumEligible
      || !Number.isInteger(releaseGate.eligibleCardCount)
      || releaseGate.eligibleCardCount < minimumEligible
      || !Array.isArray(releaseGate.globalBlockers)
      || releaseGate.globalBlockers.length !== 0
    ) {
      throw new Error("canonical DB QC receipt release binding is invalid");
    }
    if (release.id === "relaxed-launch-v1") {
      if (releaseGate.perCardExclude !== true) {
        throw new Error("relaxed DB QC receipt is not card-scoped");
      }
    } else if (
      counts.releaseBlockedQualified !== 0
      || counts.releaseReadyQualified !== counts.qualified
      || releaseGate.blockerCount !== 0
      || releaseGate.blockerCardCount !== 0
      || !sameJson(releaseGate.blockers, {})
    ) {
      throw new Error("canonical DB QC receipt contract is invalid");
    }
    Object.assign(binding, expected);
  }
  if (!sameJson(snapshot.generation.dbQc, binding)) {
    throw new Error("snapshot canonical DB QC receipt binding is invalid");
  }
  return binding;
}

function publicMediaAssets(assets) {
  return assets.map((asset) => ({
    key: asset.key,
    sha256: asset.contentSha256,
    width: asset.width,
    height: asset.height,
    variant: asset.variant,
    baseSha256: asset.baseHash,
  }));
}

function cardMediaBinding(card, assets) {
  const byVariant = Object.fromEntries(
    assets
      .filter((asset) => asset.baseHash === card.image.sha256)
      .map((asset) => [
        asset.variant,
        {
          key: asset.key,
          sha256: asset.contentSha256,
          width: asset.width,
          height: asset.height,
        },
      ]),
  );
  return {
    base: byVariant.base,
    "200": byVariant["200"],
    "600": byVariant["600"],
  };
}

function validateQcReceipt(snapshot, receipt, receiptBytes, dbQc, assets, release, releaseSnapshot) {
  const digest = sha256(receiptBytes);
  if (snapshot.generation.qcReceiptSha256 !== digest) {
    throw new Error("snapshot QC receipt SHA-256 mismatch");
  }
  const expectedCards = releaseSnapshot.cards;
  const expectedClaim = snapshot.top100.length === releaseSnapshot.requiredCount
    ? "verified-top-100"
    : "verified-top-n";
  const expectedVerifiedCount = release.id === "relaxed-launch-v1"
    ? expectedCards.length
    : snapshot.top100.length;
  if (
    receipt?.schemaVersion !== 1
    || receipt.runId !== dbQc.runId
    || receipt.generationId !== snapshot.generation.id
    || receipt.snapshotContentSha256 !== receiptSnapshotContentHash(snapshot)
    || receipt.dbQcReceiptSha256 !== dbQc.receiptSha256
    || receipt.universeCandidateSha256 !== dbQc.universeCandidateSha256
    || !sameJson(receipt.dbQc, dbQc)
    || !Number.isFinite(Date.parse(receipt.checkedAt))
    || receipt.status !== "passed"
    || receipt.claim !== expectedClaim
    || receipt.claim !== snapshot.coverage.claim
    || receipt.requestedCount !== releaseSnapshot.requiredCount
    || receipt.requestedCount !== snapshot.coverage.requestedCount
    || receipt.verifiedCount !== expectedVerifiedCount
    || receipt.verifiedCount !== snapshot.coverage.verifiedCount
    || !Array.isArray(receipt.blockers)
    || receipt.blockers.length !== 0
    || !Array.isArray(receipt.cards)
    || receipt.cards.length !== expectedCards.length
  ) {
    throw new Error("QC receipt contract is invalid");
  }
  if (release.bound) {
    const expected = releaseBinding(snapshot, release);
    if (
      receipt.releaseProfile !== expected.releaseProfile
      || receipt.policySha256 !== expected.policySha256
      || receipt.databaseFingerprint !== expected.databaseFingerprint
      || receipt.evaluationId !== expected.evaluationId
    ) {
      throw new Error("QC receipt release binding is invalid");
    }
  }
  const expectedMediaAssets = publicMediaAssets(assets);
  if (
    receipt.media?.prefix !== "market-assets/"
    || !Array.isArray(receipt.media.assets)
    || !sameJson(receipt.media.assets, expectedMediaAssets)
  ) {
    throw new Error("QC receipt media binding is invalid");
  }
  for (const [index, card] of expectedCards.entries()) {
    const decision = receipt.cards[index];
    if (
      decision?.id !== card.id
      || decision.imageSha256 !== card.image.sha256
      || decision.decision !== "passed"
      || !/^[a-f0-9]{64}$/.test(decision.evidenceSha256 ?? "")
      || !sameJson(decision.media, cardMediaBinding(card, assets))
    ) {
      throw new Error(`QC receipt card binding is invalid at index ${index}`);
    }
  }
  if (assets.length > releaseSnapshot.assetMaximum) {
    throw new Error("QC receipt exceeds release profile image asset capacity");
  }
  return digest;
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
  const release = await loadReleaseProfile(snapshot);
  const releaseSnapshot = validateReleaseSnapshotContract(snapshot, release);
  assertPublicSnapshot(snapshot, { production: true });
  const dbQcReceiptBytes = await readFile(options.dbQcReceipt);
  let dbQcReceipt;
  try {
    dbQcReceipt = JSON.parse(dbQcReceiptBytes.toString("utf8"));
  } catch {
    throw new Error("canonical DB QC receipt is not valid JSON");
  }
  const dbQc = validateDbQcReceipt(snapshot, dbQcReceipt, dbQcReceiptBytes, release);
  const qcReceiptBytes = await readFile(options.qcReceipt);
  let qcReceipt;
  try {
    qcReceipt = JSON.parse(qcReceiptBytes.toString("utf8"));
  } catch {
    throw new Error("QC receipt is not valid JSON");
  }
  const assets = await collectAssets(snapshot, options.assetsRoot);
  const qcReceiptSha256 = validateQcReceipt(
    snapshot,
    qcReceipt,
    qcReceiptBytes,
    dbQc,
    assets,
    release,
    releaseSnapshot,
  );
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
  const qcReceiptKey = `generations/${snapshot.generation.id}/qc-receipt.json`;
  const dbQcReceiptKey = `generations/${snapshot.generation.id}/db-qc-receipt.json`;
  const pointer = {
    schemaVersion: 1,
    generationId: snapshot.generation.id,
    generatedAt: snapshot.generation.generatedAt,
    snapshotKey: generationKey,
    sha256: snapshot.generation.contentSha256,
    qcReceiptKey,
    qcReceiptSha256,
    dbQcReceiptKey,
    dbQc,
    ...releaseBinding(snapshot, release),
    media: {
      prefix: "market-assets/",
      /* Worker uses base hashes to authorize base/_200/_600 paths. */
      hashes: [...new Set(assets.map((asset) => asset.baseHash))],
      assets: publicMediaAssets(assets),
      remoteVerified: Boolean(options.r2Bucket),
      remoteScope,
    },
  };
  const generationPath = path.join(options.out, ...generationKey.split("/"));
  const snapshotPayload = `${JSON.stringify(snapshot, null, 2)}\n`;
  const pointerPayload = `${JSON.stringify(pointer, null, 2)}\n`;

  await immutableWrite(generationPath, snapshotPayload, "generation");
  const localQcReceiptPath = path.join(options.out, ...qcReceiptKey.split("/"));
  await immutableWrite(localQcReceiptPath, qcReceiptBytes, "QC receipt");
  const localDbQcReceiptPath = path.join(options.out, ...dbQcReceiptKey.split("/"));
  await immutableWrite(localDbQcReceiptPath, dbQcReceiptBytes, "canonical DB QC receipt");
  const localAssetsRoot = path.join(options.out, "generations", snapshot.generation.id, "assets");
  await mkdir(localAssetsRoot, { recursive: true });
  for (const asset of assets) {
    await immutableCopy(
      asset.file,
      path.join(localAssetsRoot, path.basename(asset.file)),
      "generation asset",
    );
  }
  const persisted = JSON.parse(await readFile(generationPath, "utf8"));
  if (canonicalHash(persisted) !== pointer.sha256) throw new Error("persisted generation verification failed");
  if (sha256(await readFile(localQcReceiptPath)) !== pointer.qcReceiptSha256) {
    throw new Error("persisted QC receipt verification failed");
  }
  if (sha256(await readFile(localDbQcReceiptPath)) !== pointer.dbQc.receiptSha256) {
    throw new Error("persisted canonical DB QC receipt verification failed");
  }

  if (options.r2Bucket) {
    const verifyRoot = path.join(options.out, "remote-assets.verify");
    await mkdir(verifyRoot, { recursive: true });
    // Content-addressing makes uploads idempotent, but a local pointer is not
    // evidence that another bucket still contains the bytes. Verify every
    // referenced remote object before the generation can be promoted.
    let verifiedAssetCount = 0;
    await forEachConcurrent(assets, 6, async (asset) => {
      await r2Put(options.r2Bucket, asset.key, asset.file, asset.contentType, "public,max-age=31536000,immutable");
      const remoteAsset = path.join(verifyRoot, path.basename(asset.file));
      await r2Get(options.r2Bucket, asset.key, remoteAsset);
      if (sha256(await readFile(remoteAsset)) !== asset.contentSha256) {
        throw new Error(`remote media verification failed: ${asset.key}; latest.json was not advanced`);
      }
      verifiedAssetCount += 1;
      if (verifiedAssetCount % 50 === 0 || verifiedAssetCount === assets.length) {
        process.stdout.write(`Verified ${verifiedAssetCount}/${assets.length} remote media files.\n`);
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
    await r2Put(options.r2Bucket, qcReceiptKey, localQcReceiptPath);
    const remoteQcReceiptTemp = path.join(options.out, "remote-qc-receipt.verify.json");
    await r2Get(options.r2Bucket, qcReceiptKey, remoteQcReceiptTemp);
    if (sha256(await readFile(remoteQcReceiptTemp)) !== pointer.qcReceiptSha256) {
      throw new Error("remote QC receipt verification failed; latest.json was not advanced");
    }
    await r2Put(options.r2Bucket, dbQcReceiptKey, localDbQcReceiptPath);
    const remoteDbQcReceiptTemp = path.join(options.out, "remote-db-qc-receipt.verify.json");
    await r2Get(options.r2Bucket, dbQcReceiptKey, remoteDbQcReceiptTemp);
    if (sha256(await readFile(remoteDbQcReceiptTemp)) !== pointer.dbQc.receiptSha256) {
      throw new Error("remote canonical DB QC receipt verification failed; latest.json was not advanced");
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
      qcReceiptKey,
      qcReceiptSha256,
      dbQcReceiptKey,
      dbQcReceiptSha256: dbQc.receiptSha256,
      dbQcRunId: dbQc.runId,
      universeCandidateSha256: dbQc.universeCandidateSha256,
      ...releaseBinding(snapshot, release),
    }, null, 2)}\n`);
    const hookEnvironment = {
      CARDZ_R2_BUCKET: options.r2Bucket,
      CARDZ_CANDIDATE_POINTER_KEY: candidatePointerKey,
      CARDZ_CANDIDATE_GENERATION_ID: snapshot.generation.id,
      CARDZ_CANDIDATE_GENERATION_KEY: generationKey,
      CARDZ_CANDIDATE_QC_RECEIPT_KEY: qcReceiptKey,
      CARDZ_CANDIDATE_QC_RECEIPT_SHA256: qcReceiptSha256,
      CARDZ_CANDIDATE_DB_QC_RECEIPT_KEY: dbQcReceiptKey,
      CARDZ_CANDIDATE_DB_QC_RECEIPT_SHA256: dbQc.receiptSha256,
      CARDZ_CANDIDATE_DB_QC_RUN_ID: dbQc.runId,
      CARDZ_CANDIDATE_UNIVERSE_SHA256: dbQc.universeCandidateSha256,
      ...(release.bound ? {
        CARDZ_CANDIDATE_RELEASE_PROFILE: release.id,
        CARDZ_CANDIDATE_POLICY_SHA256: release.policySha256,
        CARDZ_CANDIDATE_DB_FINGERPRINT: snapshot.generation.dbFingerprint,
        CARDZ_CANDIDATE_EVALUATION_ID: String(snapshot.generation.evaluationId),
      } : {}),
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
    const persistedPointerBytes = await readFile(remotePointerVerify);
    const persistedPointer = JSON.parse(persistedPointerBytes.toString("utf8"));
    if (
      sha256(persistedPointerBytes) !== sha256(Buffer.from(pointerPayload)) ||
      persistedPointer.generationId !== pointer.generationId ||
      persistedPointer.snapshotKey !== pointer.snapshotKey ||
      persistedPointer.sha256 !== pointer.sha256 ||
      persistedPointer.qcReceiptKey !== pointer.qcReceiptKey ||
      persistedPointer.qcReceiptSha256 !== pointer.qcReceiptSha256 ||
      persistedPointer.dbQcReceiptKey !== pointer.dbQcReceiptKey ||
      !sameJson(persistedPointer.dbQc, pointer.dbQc) ||
      (release.bound && (
        persistedPointer.releaseProfile !== pointer.releaseProfile
        || persistedPointer.policySha256 !== pointer.policySha256
        || persistedPointer.databaseFingerprint !== pointer.databaseFingerprint
        || persistedPointer.evaluationId !== pointer.evaluationId
      )) ||
      !sameJson(persistedPointer.media?.assets, pointer.media.assets)
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
      qcReceiptKey,
      qcReceiptSha256,
      dbQcReceiptKey,
      dbQcReceiptSha256: dbQc.receiptSha256,
      dbQcRunId: dbQc.runId,
      releaseProfile: release.id,
      policySha256: release.policySha256,
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
