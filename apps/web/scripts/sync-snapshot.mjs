/* 由 live pointer 指嗰代 copy market media 落 apps/web/public/market-assets。

   2026-08-02 事故（docs/POSTMORTEM_DATA_SCATTER_20260802.md D4）：呢個檔以前
   `rmSync(publicAssets)` 之後，照住 tracked demo seed `data/public/seed-snapshot.json`
   （07-28 嗰 229 卡）抄返。live 係 433 卡代、1,299 個 asset，所以一次 `npm run build`
   刪走 1,293 個，仲要 exit 0 兼印 "Synced 229"。

   而家：讀 `data/runtime/publish-staging/latest.json` → 嗰代嘅 snapshot + assets；
   唔再 rmSync（dest 只加唔刪），冇 pointer 先至 fallback 落 tracked seed。 */

import { createHash } from "node:crypto";
import { copyFileSync, existsSync, mkdirSync, readFileSync } from "node:fs";
import { basename, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const appRoot = resolve(here, "..");
const repoRoot = resolve(appRoot, "..", "..");
const pointerPath = resolve(repoRoot, "data", "runtime", "publish-staging", "latest.json");
const seedSnapshot = resolve(repoRoot, "data", "public", "seed-snapshot.json");
const seedAssets = resolve(repoRoot, "data", "public", "market-assets");
const publicAssets = resolve(appRoot, "public", "market-assets");
const publicAssetPattern = /^\/market-assets\/([a-f0-9]{64})\.webp$/;

function fileSha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

/* 來源選擇：live pointer 優先。pointer 唔喺度（乾淨 clone / 未發佈過）先用 tracked seed，
   因為 seed 係 repo 入面唯一保證存在嘅 v2 snapshot。 */
function resolveSource() {
  if (existsSync(pointerPath)) {
    const pointer = JSON.parse(readFileSync(pointerPath, "utf8"));
    const generationId = pointer.generationId;
    if (typeof generationId !== "string" || !generationId) {
      throw new Error("publish-staging/latest.json has no generationId.");
    }
    const generationDir = resolve(repoRoot, "data", "runtime", "publish-staging", "generations", generationId);
    const snapshotPath = resolve(generationDir, "snapshot.json");
    const assetsDir = resolve(generationDir, "assets");
    if (!existsSync(snapshotPath)) throw new Error(`Generation snapshot is missing: ${generationId}`);
    if (!existsSync(assetsDir)) throw new Error(`Generation asset tree is missing: ${generationId}`);
    /* 唔好攞 pointer.sha256 同 snapshot.json 嘅檔案 bytes 比：
       `publish-snapshot.mjs:604` 寫入去嗰個係 `snapshot.generation.contentSha256`
       （canonical content hash），唔係檔案 sha。改成對 generation id，
       確認 pointer 同呢份 snapshot 講緊同一代就夠。 */
    return { label: generationId, snapshotPath, assetsDir, generationId };
  }
  if (!existsSync(seedSnapshot)) throw new Error("No published generation and no sanitized public seed snapshot.");
  return { label: "seed-snapshot (no published generation)", snapshotPath: seedSnapshot, assetsDir: seedAssets };
}

const source = resolveSource();

const parsed = JSON.parse(readFileSync(source.snapshotPath, "utf8"));
if (parsed.schemaVersion !== "2.0.0") throw new Error("Only v2 public snapshots may enter the web build.");
if (source.generationId && parsed.generation?.id !== source.generationId) {
  throw new Error(`Pointer generation ${source.generationId} does not match the snapshot it points at (${parsed.generation?.id ?? "unknown"}).`);
}
const cards = [...(parsed.top100 ?? []), ...(parsed.watchlist ?? [])];
if (!cards.length) throw new Error("Public snapshot contains no cards.");

/* 只保留內容定址嘅結構要求（src 要指得返檔、sha 要對得返路徑）——冇咗佢就搵唔到
   要抄邊個檔。圖嘅 QC/qcAt/kind 閘 2026-08-02 owner 令拆晒，唔喺呢度重造。 */
const referenced = new Map();
for (const card of cards) {
  const match = typeof card.image?.src === "string" ? card.image.src.match(publicAssetPattern) : null;
  if (!match) continue;
  const [, hash] = match;
  if (card.image.sha256 !== hash) throw new Error(`Card ${card.id ?? "unknown"} image hash does not match its content-addressed path.`);
  referenced.set(card.image.src, hash);
}
if (!referenced.size) throw new Error("Public snapshot references no content-addressed market assets.");

/* srcSet 用 `w` descriptor + sizes，`src` 唔喺候選集，所以 derivative 缺一張
   就係一張爛圖，冇 fallback。以前呢度靜靜 skip，成站爛咗都 build 綠燈。 */
const derivativeSuffixes = ["200", "600"];
const missingDerivatives = [];

for (const [publicPath, expectedHash] of referenced) {
  const filename = basename(publicPath);
  const sourceFile = resolve(source.assetsDir, filename);
  if (!existsSync(sourceFile)) throw new Error(`Referenced public image is missing: ${filename}`);
  if (fileSha256(sourceFile) !== expectedHash) throw new Error(`Referenced public image failed SHA-256 verification: ${filename}`);
  const stem = filename.replace(/\.webp$/, "");
  for (const suffix of derivativeSuffixes) {
    if (!existsSync(resolve(source.assetsDir, `${stem}_${suffix}.webp`))) missingDerivatives.push(`${stem}_${suffix}.webp`);
  }
}

if (missingDerivatives.length) {
  const sample = missingDerivatives.slice(0, 10).join("\n  ");
  throw new Error(
    `${missingDerivatives.length} responsive derivative(s) missing for ${referenced.size} referenced assets. ` +
      `<img srcSet> uses w descriptors, so a missing derivative renders as a broken image with no src fallback. ` +
      `Run: python -X utf8 pipelines/build_asset_derivatives.py --write\n  ${sample}` +
      (missingDerivatives.length > 10 ? `\n  ...and ${missingDerivatives.length - 10} more` : ""),
  );
}

/* 冇 rmSync。dest 係純 copy-over 目標：多咗嘅舊 sha 係死重量，唔係損壞；
   刪咗就係 2026-08-02 嗰單 1,293 檔蒸發。 */
if (process.env.CARDZ_CLOUDFLARE_BUILD !== "1") {
  mkdirSync(publicAssets, { recursive: true });
  for (const publicPath of referenced.keys()) {
    const filename = basename(publicPath);
    copyFileSync(resolve(source.assetsDir, filename), resolve(publicAssets, filename));
    /* 縮圖 derivative 一齊 copy（唔 hash-verify）。上面已經驗證過齊晒，
       所以呢度唔再 existsSync —— 真係唔見要即刻炸，唔准靜靜跳過。 */
    const stem = filename.replace(/\.webp$/, "");
    for (const suffix of derivativeSuffixes) {
      copyFileSync(resolve(source.assetsDir, `${stem}_${suffix}.webp`), resolve(publicAssets, `${stem}_${suffix}.webp`));
    }
  }
}

process.stdout.write(
  process.env.CARDZ_CLOUDFLARE_BUILD === "1"
    ? `Verified ${referenced.size} market assets + ${referenced.size * derivativeSuffixes.length} derivatives from ${source.label} without bundling them into Cloudflare static assets.\n`
    : `Synced ${referenced.size} market assets + ${referenced.size * derivativeSuffixes.length} derivatives from ${source.label} for local preview.\n`,
);
