import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync, rmSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "..");
const repoRoot = resolve(appRoot, "..", "..");
const sourceSnapshot = resolve(repoRoot, "data", "public", "seed-snapshot.json");
const sourceAssets = resolve(repoRoot, "data", "public", "market-assets");
const publicAssets = resolve(appRoot, "public", "market-assets");
const publicAssetPattern = /^\/market-assets\/([a-f0-9]{64})\.webp$/;

function fileSha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

if (!existsSync(sourceSnapshot)) throw new Error("Sanitized public seed snapshot is required.");

const parsed = JSON.parse(readFileSync(sourceSnapshot, "utf8"));
if (parsed.schemaVersion !== "2.0.0") throw new Error("Only v2 public snapshots may enter the web build.");
const cards = [...(parsed.top100 ?? []), ...(parsed.watchlist ?? [])];
if (!cards.length) throw new Error("Public snapshot contains no cards.");

const referenced = new Map();
for (const card of cards) {
  const match = typeof card.image?.src === "string" ? card.image.src.match(publicAssetPattern) : null;
  if (card.image?.kind !== "raw_front" || typeof card.image?.qcAt !== "string" || !card.image.qcAt || !match) {
    throw new Error(`Card ${card.id ?? "unknown"} does not have a QC-passed raw_front image.`);
  }
  const [, hash] = match;
  if (card.image.sha256 !== hash) throw new Error(`Card ${card.id ?? "unknown"} image hash does not match its content-addressed path.`);
  referenced.set(card.image.src, hash);
}

for (const [publicPath, expectedHash] of referenced) {
  const filename = basename(publicPath);
  const source = resolve(sourceAssets, filename);
  if (!existsSync(source)) throw new Error(`Referenced public image is missing: ${filename}`);
  if (fileSha256(source) !== expectedHash) throw new Error(`Referenced public image failed SHA-256 verification: ${filename}`);
}

if (existsSync(publicAssets)) rmSync(publicAssets, { recursive: true, force: true });
mkdirSync(publicAssets, { recursive: true });

for (const publicPath of referenced.keys()) {
  const filename = basename(publicPath);
  const source = resolve(sourceAssets, filename);
  copyFileSync(source, resolve(publicAssets, filename));
}

process.stdout.write(`Synced ${referenced.size} referenced raw_front assets.\n`);
