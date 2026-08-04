import { assertPublicSnapshot, type PublicMarketSnapshot } from "@cardz/market-data";
import { cloudflareEnv, isNodeRuntime } from "./cloudflare-env";
import { marketAssetHash, marketAssetObjectKey } from "./market-media";
import { isOperatorMode, loadOperatorSnapshotFromDb } from "./operator-db-snapshot";
import { getSeedSnapshot, normaliseSnapshot } from "./snapshot";
import { parseSnapshotPointer, type SnapshotPointer } from "./snapshot-pointer";
import type { Grader, MarketCardView, MarketViewSnapshot } from "./types";

interface R2Body {
  text(): Promise<string>;
}

interface MarketBucket {
  get(key: string): Promise<R2Body | null>;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function stableValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(stableValue);
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, stableValue(value[key])]),
    );
  }
  return value;
}

async function receiptSnapshotContentSha256(
  snapshot: PublicMarketSnapshot,
): Promise<string> {
  const normalized = structuredClone(snapshot);
  normalized.generation.contentSha256 = "";
  normalized.generation.qcReceiptSha256 = "";
  const bytes = new TextEncoder().encode(JSON.stringify(stableValue(normalized)));
  return Array.from(
    new Uint8Array(await crypto.subtle.digest("SHA-256", bytes)),
    (byte) => byte.toString(16).padStart(2, "0"),
  ).join("");
}

const RECEIPT_MEDIA_SPECS = {
  base: { suffix: "", width: null, height: null },
  "200": { suffix: "_200", width: 200, height: 280 },
  "600": { suffix: "_600", width: 429, height: 600 },
} as const;

function assertProductionReceiptBindings(
  snapshot: PublicMarketSnapshot,
  receipt: Record<string, unknown>,
  pointer: SnapshotPointer,
  expectedSnapshotContentSha256: string,
): void {
  const dbQc = snapshot.generation.dbQc;
  const receiptDbQc = receipt.dbQc;
  const pointerDbQc = (pointer as unknown as Record<string, unknown>).dbQc;
  if (
    !dbQc
    || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/.test(dbQc.runId)
    || !/^[a-f0-9]{64}$/.test(dbQc.receiptSha256)
    || !/^[a-f0-9]{64}$/.test(dbQc.universeCandidateSha256)
    || dbQc.database !== "cardz_market_cap"
    || receipt.runId !== dbQc.runId
    || receipt.dbQcReceiptSha256 !== dbQc.receiptSha256
    || receipt.universeCandidateSha256 !== dbQc.universeCandidateSha256
    || receipt.snapshotContentSha256 !== expectedSnapshotContentSha256
    || !isRecord(receiptDbQc)
    || !isRecord(pointerDbQc)
    || ["runId", "receiptSha256", "database", "universeCandidateSha256"].some(
      (field) => receiptDbQc[field] !== dbQc[field as keyof typeof dbQc]
        || pointerDbQc[field] !== dbQc[field as keyof typeof dbQc],
    )
  ) {
    throw new Error("Snapshot QC receipt DB/final snapshot binding is invalid");
  }

  const expectedCards = [...snapshot.top100, ...snapshot.watchlist];
  const receiptCards = receipt.cards;
  const expectedAssets = new Map<string, Record<string, unknown>>();
  if (!Array.isArray(receiptCards)) {
    throw new Error("Snapshot QC receipt media binding is invalid");
  }
  for (const [index, card] of expectedCards.entries()) {
    const decision = receiptCards[index];
    const cardMedia = isRecord(decision) ? decision.media : null;
    if (!isRecord(cardMedia)) {
      throw new Error(`Snapshot QC receipt media binding is invalid at index ${index}`);
    }
    for (const [variant, spec] of Object.entries(RECEIPT_MEDIA_SPECS)) {
      const entry = cardMedia[variant];
      const key = `market-assets/${card.image.sha256}${spec.suffix}.webp`;
      const width = variant === "base" ? card.image.width : spec.width;
      const height = variant === "base" ? card.image.height : spec.height;
      if (
        !isRecord(entry)
        || entry.key !== key
        || typeof entry.sha256 !== "string"
        || !/^[a-f0-9]{64}$/.test(entry.sha256)
        || entry.width !== width
        || entry.height !== height
        || (variant === "base" && entry.sha256 !== card.image.sha256)
      ) {
        throw new Error(`Snapshot QC receipt media binding is invalid at index ${index}`);
      }
      expectedAssets.set(key, {
        key,
        sha256: entry.sha256,
        width,
        height,
        variant,
        baseSha256: card.image.sha256,
      });
    }
  }

  const receiptMedia = receipt.media;
  const receiptAssets = isRecord(receiptMedia) ? receiptMedia.assets : null;
  if (
    !isRecord(receiptMedia)
    || receiptMedia.prefix !== "market-assets/"
    || !Array.isArray(receiptAssets)
    || receiptAssets.length !== expectedAssets.size
  ) {
    throw new Error("Snapshot QC receipt media manifest is invalid");
  }
  const receiptAssetsByKey = new Map<string, Record<string, unknown>>();
  for (const asset of receiptAssets) {
    if (!isRecord(asset) || typeof asset.key !== "string") {
      throw new Error("Snapshot QC receipt media manifest is invalid");
    }
    receiptAssetsByKey.set(asset.key, asset);
  }
  if (receiptAssetsByKey.size !== receiptAssets.length) {
    throw new Error("Snapshot QC receipt media manifest is invalid");
  }
  for (const [key, expected] of expectedAssets) {
    const actual = receiptAssetsByKey.get(key);
    if (
      !actual
      || Object.entries(expected).some(([field, value]) => actual[field] !== value)
    ) {
      throw new Error("Snapshot QC receipt media manifest is invalid");
    }
  }

  const pointerAssets = pointer.media.assets as unknown[];
  if (pointerAssets.length !== receiptAssets.length) {
    throw new Error("Snapshot pointer media does not match its QC receipt");
  }
  for (const asset of pointerAssets) {
    const receiptAsset = isRecord(asset) && typeof asset.key === "string"
      ? receiptAssetsByKey.get(asset.key)
      : null;
    if (
      !isRecord(asset)
      || !receiptAsset
      || ["key", "sha256", "width", "height", "variant", "baseSha256"].some(
        (field) => asset[field] !== receiptAsset[field],
      )
    ) {
      throw new Error("Snapshot pointer media does not match its QC receipt");
    }
  }
}

async function assertPublicQcReceipt(
  snapshot: PublicMarketSnapshot,
  serialized: string,
  pointer: SnapshotPointer,
): Promise<void> {
  let receipt: unknown;
  try {
    receipt = JSON.parse(serialized);
  } catch {
    throw new Error("Snapshot QC receipt is not valid JSON");
  }
  const expectedCards = [...snapshot.top100, ...snapshot.watchlist];
  const expectedClaim = snapshot.top100.length === 100 ? "verified-top-100" : "verified-top-n";
  if (
    !isRecord(receipt)
    || receipt.schemaVersion !== 1
    || receipt.generationId !== snapshot.generation.id
    || typeof receipt.checkedAt !== "string"
    || !Number.isFinite(Date.parse(receipt.checkedAt))
    || receipt.status !== "passed"
    || receipt.claim !== expectedClaim
    || receipt.claim !== snapshot.coverage.claim
    || receipt.requestedCount !== 100
    || receipt.requestedCount !== snapshot.coverage.requestedCount
    || receipt.verifiedCount !== snapshot.top100.length
    || receipt.verifiedCount !== snapshot.coverage.verifiedCount
    || !Array.isArray(receipt.blockers)
    || receipt.blockers.length !== 0
    || !Array.isArray(receipt.cards)
    || receipt.cards.length !== expectedCards.length
  ) {
    throw new Error("Snapshot QC receipt contract is invalid");
  }
  for (const [index, card] of expectedCards.entries()) {
    const decision = receipt.cards[index];
    if (
      !isRecord(decision)
      || decision.id !== card.id
      || decision.imageSha256 !== card.image.sha256
      || decision.decision !== "passed"
      || typeof decision.evidenceSha256 !== "string"
      || !/^[a-f0-9]{64}$/.test(decision.evidenceSha256)
    ) {
      throw new Error(`Snapshot QC receipt card binding is invalid at index ${index}`);
    }
  }
  if (
    snapshot.generation.mode === "production"
    && snapshot.generation.productionEligible
  ) {
    assertProductionReceiptBindings(
      snapshot,
      receipt,
      pointer,
      await receiptSnapshotContentSha256(snapshot),
    );
  }
}

function assertRemoteProductionPointer(
  snapshot: PublicMarketSnapshot,
  pointer: SnapshotPointer,
): void {
  if (
    snapshot.generation.mode === "production"
    && snapshot.generation.productionEligible
    && !pointer.media.remoteVerified
  ) {
    throw new Error("Remote production snapshot is not remotely verified");
  }
}

let runtimeLastGood: MarketViewSnapshot | null = null;

interface NodeSnapshotRecord {
  sourcePath: string;
  sourceKey: string;
  snapshot: MarketViewSnapshot;
  pointer: SnapshotPointer | null;
  assetsRoot: string | null;
}

export interface NodeMarketAsset {
  body: ArrayBuffer;
  generation: string;
}

let nodeSnapshotRecord: NodeSnapshotRecord | null = null;

function ranked(cards: MarketCardView[]): MarketCardView[] {
  return [...cards]
    .sort((a, b) => (b.marketCap.value ?? 0) - (a.marketCap.value ?? 0))
    .slice(0, 100)
    .map((card, index) => ({ ...card, rank: index + 1, viewRank: index + 1 }));
}

function scopedCoverage(count: number): MarketViewSnapshot["coverage"] {
  return {
    claim: count === 100 ? "verified-top-100" : "verified-top-n",
    requestedCount: 100,
    verifiedCount: count,
  };
}

function listCard(card: MarketCardView): MarketCardView {
  return {
    ...card,
    story: { en: "", "zh-TW": "", "zh-CN": "", ja: "", ko: "" },
    historyDaily: card.historyDaily
      .filter((point) => point.trackedSalesValueUsd !== null)
      .slice(-14)
      .map(({ at, trackedSalesValueUsd }) => ({
        at,
        priceUsd: null,
        priceStatus: "unavailable" as const,
        trackedSalesValueUsd: trackedSalesValueUsd === null ? null : Math.round(trackedSalesValueUsd),
        trackedSalesCount: null,
        salesCoverage: "partial" as const,
      })),
  };
}

/*
 * Node server（WSL / AWS / Docker）路徑：冇 R2 binding。Production 優先用
 * MARKET_DATA_POINTER_PATH 指住 publisher 嘅 versioned latest.json；pointer 一轉，
 * 下一個 request 自動載入新 generation。MARKET_DATA_SNAPSHOT_PATH 保留畀單檔
 * local/dev 用途，會按 mtime + size 重載。兩條路壞檔都保留同一 source 嘅
 * last-known-good，唔會用半寫 snapshot 取代已驗證 generation。
 */

/*
 * `next build` 會 prerender 靜態頁（/sitemap.xml 等），嗰陣一定行得到呢條路，
 * 而嗰刻 build context 入面通常仲係 demo seed。build 期 throw = build 直接炸，
 * 所以 demo 閘只喺 runtime 生效，build 期放行。
 */
function isBuildPhase(): boolean {
  return process.env.NEXT_PHASE === "phase-production-build";
}

function allowDemoSnapshot(): boolean {
  return process.env.MARKET_DATA_ALLOW_DEMO === "true";
}

function referencedSnapshotHashes(snapshot: MarketViewSnapshot): Set<string> {
  const hashes = new Set<string>();
  for (const card of [...snapshot.top100, ...snapshot.watchlist]) {
    const filename = card.image.url.split("/").at(-1);
    const hash = filename ? marketAssetHash(filename) : null;
    if (hash) hashes.add(hash);
  }
  return hashes;
}

async function readCanonicalSnapshot(path: string): Promise<PublicMarketSnapshot> {
  const { readFile } = await import("node:fs/promises");
  const canonical = JSON.parse(await readFile(path, "utf8")) as PublicMarketSnapshot;
  assertPublicSnapshot(canonical, { production: !allowDemoSnapshot() });
  return canonical;
}

async function loadPointerSnapshot(pointerPath: string): Promise<NodeSnapshotRecord> {
  const { readFile } = await import("node:fs/promises");
  const { createHash } = await import("node:crypto");
  const { dirname, join } = await import("node:path");
  const pointer = parseSnapshotPointer(JSON.parse(await readFile(pointerPath, "utf8")));
  const sourceKey = `pointer:${pointer.generationId}:${pointer.sha256}`;
  if (
    nodeSnapshotRecord?.sourcePath === pointerPath
    && nodeSnapshotRecord.sourceKey === sourceKey
  ) {
    return nodeSnapshotRecord;
  }

  const publishRoot = dirname(pointerPath);
  const snapshotPath = join(publishRoot, ...pointer.snapshotKey.split("/"));
  const receiptPath = join(publishRoot, ...pointer.qcReceiptKey.split("/"));
  const canonical = await readCanonicalSnapshot(snapshotPath);
  const receiptBytes = await readFile(receiptPath);
  await assertPublicQcReceipt(canonical, receiptBytes.toString("utf8"), pointer);
  const receiptSha256 = createHash("sha256")
    .update(receiptBytes)
    .digest("hex");
  if (
    canonical.generation.id !== pointer.generationId
    || canonical.generation.contentSha256 !== pointer.sha256
    || canonical.generation.qcReceiptSha256 !== pointer.qcReceiptSha256
    || receiptSha256 !== pointer.qcReceiptSha256
  ) {
    throw new Error("Local snapshot generation does not match latest pointer");
  }
  const record: NodeSnapshotRecord = {
    sourcePath: pointerPath,
    sourceKey,
    snapshot: normaliseSnapshot(canonical),
    pointer,
    assetsRoot: join(publishRoot, "generations", pointer.generationId, "assets"),
  };
  nodeSnapshotRecord = record;
  return record;
}

async function loadSingleFileSnapshot(snapshotPath: string): Promise<NodeSnapshotRecord> {
  const { stat } = await import("node:fs/promises");
  const metadata = await stat(snapshotPath);
  const sourceKey = `file:${metadata.mtimeMs}:${metadata.size}`;
  if (
    nodeSnapshotRecord?.sourcePath === snapshotPath
    && nodeSnapshotRecord.sourceKey === sourceKey
  ) {
    return nodeSnapshotRecord;
  }
  const canonical = await readCanonicalSnapshot(snapshotPath);
  const record: NodeSnapshotRecord = {
    sourcePath: snapshotPath,
    sourceKey,
    snapshot: normaliseSnapshot(canonical),
    pointer: null,
    assetsRoot: process.env.MARKET_DATA_ASSETS_PATH?.trim() || null,
  };
  nodeSnapshotRecord = record;
  return record;
}

async function loadNodeSnapshotRecord(): Promise<NodeSnapshotRecord> {
  const pointerPath = process.env.MARKET_DATA_POINTER_PATH?.trim();
  const snapshotPath = process.env.MARKET_DATA_SNAPSHOT_PATH?.trim();
  if (!pointerPath && !snapshotPath) {
    /*
     * 冇 runtime pointer/snapshot 就用 build 時燒死入 bundle 嗰份。
     * git 入面嗰份 data/public/seed-snapshot.json 按硬規矩永遠係 demo placeholder，
     * 所以 clone → docker build → 出街 呢條路預設會派 360 張假卡，而且 200 OK、
     * 冇 error、冇 log、冇 alert——demo 扮真數據係最壞嘅失敗模式，寧願唔出。
     *
     * normaliseSnapshot() 已經幫我哋計咗：mode === "canonical" 等同
     * generation.mode === "production" && generation.productionEligible。
     */
    const seed = getSeedSnapshot();
    const demoAllowed = allowDemoSnapshot();
    if (seed.mode !== "canonical" && !demoAllowed && !isBuildPhase()) {
      throw new Error(
        `Refusing to serve non-production data. The snapshot compiled into this build is generation ` +
          `"${seed.generation}" (mode=${seed.mode}); it is a demo placeholder, not real market data. ` +
          `Fix: rebuild the image with a production data/public/seed-snapshot.json in the build context ` +
          `(see docs/AWS_DEPLOY.md "Shipping real data"), or point MARKET_DATA_SNAPSHOT_PATH at a ` +
          `production snapshot file. To serve the demo placeholder deliberately, set MARKET_DATA_ALLOW_DEMO=true.`,
      );
    }
    return {
      sourcePath: "compiled-seed",
      sourceKey: `seed:${seed.generation}`,
      snapshot: seed,
      pointer: null,
      assetsRoot: null,
    };
  }

  const sourcePath = pointerPath || snapshotPath!;
  try {
    if (pointerPath) return await loadPointerSnapshot(pointerPath);
    return await loadSingleFileSnapshot(snapshotPath!);
  } catch (error) {
    if (nodeSnapshotRecord?.sourcePath === sourcePath) return nodeSnapshotRecord;
    throw error;
  }
}

async function loadNodeSnapshot(): Promise<MarketViewSnapshot> {
  return (await loadNodeSnapshotRecord()).snapshot;
}


async function operatorAssetsRoots(): Promise<string[]> {
  const { existsSync } = await import("node:fs");
  const { join, resolve } = await import("node:path");
  const roots: string[] = [];
  const envRoot = (process.env.CARDZ_REPO_ROOT || "").trim();
  const candidates = [
    envRoot,
    process.cwd(),
    resolve(process.cwd(), ".."),
    resolve(process.cwd(), "..", ".."),
    "C:/Users/jackson0202/Documents/Playground/cardz-market-cap",
  ].filter(Boolean);
  for (const root of candidates) {
    const assetDir = join(root, "data", "public", "market-assets");
    if (existsSync(assetDir) && !roots.includes(assetDir)) roots.push(assetDir);
  }
  const envAssets = (process.env.MARKET_DATA_ASSETS_PATH || "").trim();
  if (envAssets && existsSync(envAssets) && !roots.includes(envAssets)) roots.push(envAssets);
  return roots;
}

async function loadOperatorMarketAsset(
  asset: string,
  key: string,
  hash: string,
): Promise<NodeMarketAsset | null> {
  const { readFile } = await import("node:fs/promises");
  const { join } = await import("node:path");
  const filename = key.slice("market-assets/".length);
  for (const root of await operatorAssetsRoots()) {
    try {
      const contents = await readFile(join(root, filename));
      return {
        body: contents.buffer.slice(
          contents.byteOffset,
          contents.byteOffset + contents.byteLength,
        ) as ArrayBuffer,
        generation: `operator-asset:${hash.slice(0, 12)}`,
      };
    } catch {
      // try next root
    }
  }
  return null;
}

export async function loadNodeMarketAsset(asset: string): Promise<NodeMarketAsset | null> {
  const key = marketAssetObjectKey(asset);
  const hash = marketAssetHash(asset);
  if (!key || !hash) return null;

  // Operator dual-mode: content-addressed warehouse under data/public/market-assets.
  // Does not require CARDZ_RUNTIME=node (local Next dev uses this path).
  if (isOperatorMode()) {
    return loadOperatorMarketAsset(asset, key, hash);
  }

  // Product node path still requires explicit CARDZ_RUNTIME=node.
  if (!isNodeRuntime()) return null;

  const record = await loadNodeSnapshotRecord();
  const authorizedAsset = record.pointer?.media.assets.find((candidate) => candidate.key === key);
  const allowedHashes = record.pointer ? null : referencedSnapshotHashes(record.snapshot);
  if (
    (record.pointer ? !authorizedAsset : !allowedHashes?.has(hash))
    || !record.assetsRoot
  ) {
    return null;
  }

  const { readFile } = await import("node:fs/promises");
  const { createHash } = await import("node:crypto");
  const { join } = await import("node:path");
  try {
    const contents = await readFile(join(record.assetsRoot, key.slice("market-assets/".length)));
    if (
      authorizedAsset
      && createHash("sha256").update(contents).digest("hex") !== authorizedAsset.sha256
    ) {
      return null;
    }
    return {
      body: contents.buffer.slice(
        contents.byteOffset,
        contents.byteOffset + contents.byteLength,
      ) as ArrayBuffer,
      generation: record.snapshot.generation,
    };
  } catch {
    return null;
  }
}

export async function loadMarketSnapshot(): Promise<MarketViewSnapshot> {
  // Dual-mode: operator reads MySQL live; product stays snapshot-only.
  if (isOperatorMode()) {
    return loadOperatorSnapshotFromDb();
  }
  if (process.env.NODE_ENV === "development") {
    /*
     * dev 預設照舊派 demo seed；設咗 runtime pointer/snapshot 先改行 node 路徑。
     */
    if (
      process.env.MARKET_DATA_POINTER_PATH?.trim()
      || process.env.MARKET_DATA_SNAPSHOT_PATH?.trim()
    ) {
      return loadNodeSnapshot();
    }
    return getSeedSnapshot();
  }
  if (isNodeRuntime()) return loadNodeSnapshot();
  let allowDemo = process.env.MARKET_DATA_ALLOW_DEMO === "true";
  try {
    const environment = await cloudflareEnv<{
      MARKET_DATA?: MarketBucket;
      MARKET_DATA_ALLOW_DEMO?: string;
      MARKET_DATA_POINTER_KEY?: string;
    }>();
    if (!environment) throw new Error("Cloudflare bindings unavailable");
    allowDemo = environment.MARKET_DATA_ALLOW_DEMO === "true" || allowDemo;
    const bucket = environment.MARKET_DATA;
    if (!bucket) throw new Error("Market data binding unavailable");
    const pointerKey = environment.MARKET_DATA_POINTER_KEY ?? process.env.MARKET_DATA_POINTER_KEY ?? "latest.json";
    const pointerObject = await bucket.get(pointerKey);
    if (!pointerObject) throw new Error("Snapshot pointer unavailable");
    const pointer = parseSnapshotPointer(JSON.parse(await pointerObject.text()));
    const snapshotObject = await bucket.get(pointer.snapshotKey);
    if (!snapshotObject) throw new Error("Snapshot generation unavailable");
    const receiptObject = await bucket.get(pointer.qcReceiptKey);
    if (!receiptObject) throw new Error("Snapshot QC receipt unavailable");
    const serialized = await snapshotObject.text();
    const receiptSerialized = await receiptObject.text();
    const receiptBytes = new TextEncoder().encode(receiptSerialized);
    const receiptSha256 = Array.from(
      new Uint8Array(await crypto.subtle.digest("SHA-256", receiptBytes)),
      (byte) => byte.toString(16).padStart(2, "0"),
    ).join("");
    const canonical = JSON.parse(serialized) as PublicMarketSnapshot;
    assertPublicSnapshot(canonical, { production: !allowDemo });
    await assertPublicQcReceipt(canonical, receiptSerialized, pointer);
    assertRemoteProductionPointer(canonical, pointer);
    if (
      canonical.generation.id !== pointer.generationId
      || canonical.generation.contentSha256 !== pointer.sha256
      || canonical.generation.qcReceiptSha256 !== pointer.qcReceiptSha256
      || receiptSha256 !== pointer.qcReceiptSha256
    ) {
      throw new Error("Snapshot generation does not match latest pointer");
    }
    const snapshot = normaliseSnapshot(canonical);
    if (canonical.generation.mode === "production" && canonical.generation.productionEligible) runtimeLastGood = snapshot;
    return snapshot;
  } catch (error) {
    if (runtimeLastGood) return runtimeLastGood;
    if (allowDemo) return getSeedSnapshot();
    if (error instanceof Error) throw error;
    throw new Error("Market snapshot unavailable");
  }
}

export function scopeSnapshot(
  snapshot: MarketViewSnapshot,
  scope: "all" | "pokemon" | "one-piece" | "watchlist",
): MarketViewSnapshot {
  if (scope === "all") return { ...snapshot, top100: snapshot.top100.map(listCard), watchlist: [] };
  if (scope === "watchlist") {
    const cards = snapshot.watchlist.filter((card) => card.marketRank >= 101 && card.marketRank <= 300).map(listCard);
    return { ...snapshot, coverage: scopedCoverage(cards.length), top100: cards, watchlist: [] };
  }
  const expected = scope === "pokemon" ? "Pokémon" : "One Piece";
  const candidates = [...snapshot.top100, ...snapshot.watchlist].filter((card) => card.tcg === expected);
  const cards = ranked(candidates).map(listCard);
  return { ...snapshot, coverage: scopedCoverage(cards.length), top100: cards, watchlist: [] };
}

export function singleCardSnapshot(snapshot: MarketViewSnapshot, id: string): MarketViewSnapshot {
  const card = [...snapshot.top100, ...snapshot.watchlist].find((candidate) => candidate.id === id);
  const cards = card ? [card] : [];
  return { ...snapshot, coverage: scopedCoverage(cards.length), top100: cards, watchlist: [] };
}

export function graderSnapshot(snapshot: MarketViewSnapshot, grader: Grader): MarketViewSnapshot {
  const cards = [...snapshot.top100, ...snapshot.watchlist]
    .filter((card) => card.graderPopulations[grader].topGradePopulation.value !== null)
    .sort((a, b) => (b.graderPopulations[grader].topGradePopulation.value ?? 0) - (a.graderPopulations[grader].topGradePopulation.value ?? 0))
    .slice(0, 100)
    .map((card, index) => ({ ...card, rank: index + 1, viewRank: index + 1 }));
  return { ...snapshot, coverage: scopedCoverage(cards.length), top100: cards.map(listCard), watchlist: [] };
}
