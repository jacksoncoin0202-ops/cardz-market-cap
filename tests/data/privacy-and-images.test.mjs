import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";
import { promisify } from "node:util";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const execute = promisify(execFile);
// Ubuntu 24.04 冇 /usr/bin/python，Windows 嘅 python3 又係 Store 假 alias，所以兩邊各用各嘅名。
const PYTHON = process.env.CARDZ_PYTHON ?? (process.platform === "win32" ? "python" : "python3");

test("public seed contains no provider identity or source URL", async () => {
  const raw = await readFile(path.join(root, "data/public/seed-snapshot.json"), "utf8");
  for (const pattern of [/g10[-_]/i, /gemrate/i, /source[_-]?url/i, /provider[_-]?id/i, /https?:\/\//i]) {
    assert.equal(pattern.test(raw), false, `matched ${pattern}`);
  }
});

test("all referenced images are content-addressed and hash-valid", async () => {
  const snapshot = JSON.parse(await readFile(path.join(root, "data/public/seed-snapshot.json"), "utf8"));
  const cards = [...snapshot.top100, ...snapshot.watchlist];
  for (const card of cards) {
    const filename = path.basename(card.image.src);
    const contents = await readFile(path.join(root, "data/public/market-assets", filename));
    const hash = createHash("sha256").update(contents).digest("hex");
    assert.equal(hash, card.image.sha256);
    assert.equal(filename.startsWith(hash), true);
    assert.equal(card.image.kind, "raw_front");
  }
});

test("every public raw front carries exact resolver evidence and an explicit QC state", async () => {
  const snapshot = JSON.parse(await readFile(path.join(root, "data/public/seed-snapshot.json"), "utf8"));
  assert.ok(snapshot.top100.every((card) => card.image.kind === "raw_front"));
  const manifest = JSON.parse(await readFile(path.join(root, "manifests/image-qc.json"), "utf8"));
  const publicRecords = manifest.records.filter((record) => record.publicAllowed);
  assert.ok(publicRecords.length >= 100);
  for (const record of publicRecords) {
    assert.equal(record.imageKind, "raw_front");
    assert.ok(["metadata_exact_unreviewed", "human_or_vision_confirmed"].includes(record.semanticMatchStatus));
    assert.match(record.resolverEvidence.sourceContentSha256, /^[0-9a-f]{64}$/);
    assert.equal(record.resolverEvidence.collectorMatch, true);
    assert.equal(record.resolverEvidence.languageMetadataMatch, true);
    assert.equal(record.resolverEvidence.tcgMetadataMatch, true);
  }
});

test("strict production image gate rejects metadata-only demo QC", async () => {
  const error = await execute(PYTHON, ["pipelines/verify_images.py", "--strict-semantic"], { cwd: root })
    .then(() => null)
    .catch((failure) => failure);
  assert.ok(error, "strict gate unexpectedly accepted metadata-only QC");
  assert.match(`${error.stdout ?? ""}${error.stderr ?? ""}`, /human_or_vision_confirmed/);
});

test("Cloudflare builds keep raw-front media behind the pointer-authorized Worker route", async () => {
  const syncScript = await readFile(path.join(root, "apps/web/scripts/sync-snapshot.mjs"), "utf8");
  const buildScript = await readFile(path.join(root, "apps/web/scripts/cloudflare-build.mjs"), "utf8");
  assert.match(syncScript, /CARDZ_CLOUDFLARE_BUILD/);
  assert.match(buildScript, /CARDZ_CLOUDFLARE_BUILD:\s*"1"/);
  assert.match(buildScript, /card-placeholder\.svg/);
  assert.match(buildScript, /renameSync\(publicRoot, publicBackup\)/);
  assert.doesNotMatch(buildScript, /shell:\s*true/);
});
