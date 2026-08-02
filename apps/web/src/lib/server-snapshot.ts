import { type PublicMarketSnapshot } from "@cardz/market-data";
import { cloudflareEnv, isNodeRuntime } from "./cloudflare-env";
import { marketAssetHash, marketAssetObjectKey } from "./market-media";
import { getSeedSnapshot, normaliseSnapshot } from "./snapshot";
import { parseSnapshotPointer, type SnapshotPointer } from "./snapshot-pointer";
import type { Grader, MarketCardView, MarketViewSnapshot } from "./types";

interface R2Body {
  text(): Promise<string>;
}

interface MarketBucket {
  get(key: string): Promise<R2Body | null>;
}

// serve 層 QC receipt／production binding／remote pointer 驗證函數已全數剷除
// （owner 2026-08-02 全拆令）。要裝返去 git history 搵。

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
  // assertPublicSnapshot serve gate 已剷（owner 2026-08-02 全拆令）。
  return JSON.parse(await readFile(path, "utf8")) as PublicMarketSnapshot;
}

async function loadPointerSnapshot(pointerPath: string): Promise<NodeSnapshotRecord> {
  const { readFile } = await import("node:fs/promises");
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
  const canonical = await readCanonicalSnapshot(snapshotPath);
  // receipt 驗證＋pointer↔generation hash 對數已剷（owner 2026-08-02 全拆令）。
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

export async function loadNodeMarketAsset(asset: string): Promise<NodeMarketAsset | null> {
  const key = marketAssetObjectKey(asset);
  const hash = marketAssetHash(asset);
  if (!key || !hash || !isNodeRuntime()) return null;

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
    const serialized = await snapshotObject.text();
    const canonical = JSON.parse(serialized) as PublicMarketSnapshot;
    // serve 層 assert／receipt 驗證／pointer 對數全部已剷（owner 2026-08-02 全拆令）。
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
    const cards = snapshot.watchlist.map(listCard);
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
