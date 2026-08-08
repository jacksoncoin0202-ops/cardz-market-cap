import type { PublicMarketSnapshot } from "@cardz/market-data";
import { marketAssetObjectKey } from "./market-media";
import { normaliseSnapshot } from "./snapshot";
import type { MarketCardView, MarketViewSnapshot } from "./types";

const DEFAULT_SNAPSHOT_PATH = "data/public/seed-snapshot.json";
const MARKET_ASSETS_PATH = "data/public/market-assets";

export interface NodeMarketAsset {
  body: ArrayBuffer;
  generation: string;
}

let snapshotPromise: Promise<MarketViewSnapshot> | null = null;

function canonicalCards(snapshot: MarketViewSnapshot): MarketCardView[] {
  return [...snapshot.top100, ...snapshot.watchlist]
    .sort((a, b) => a.marketRank - b.marketRank);
}

function gameView(cards: MarketCardView[], tcg: "Pokémon" | "One Piece"): MarketCardView[] {
  return cards
    .filter((card) => card.tcg === tcg)
    .slice(0, 100)
    .map((card, index) => ({ ...card, rank: index + 1, viewRank: index + 1 }));
}

function scopedCoverage(count: number, requestedCount = 100): MarketViewSnapshot["coverage"] {
  return {
    claim: count === 100 && requestedCount === 100 ? "verified-top-100" : "verified-top-n",
    requestedCount,
    verifiedCount: count,
  };
}

function listCard(card: MarketCardView): MarketCardView {
  return {
    ...card,
    story: { ...card.story },
    historyDaily: card.historyDaily.map((point) => ({ ...point })),
  };
}

async function repoDataPath(relativePath: string): Promise<string> {
  const { basename, dirname, resolve } = await import("node:path");
  const configuredRoot = process.env.CARDZ_REPO_ROOT?.trim();
  if (configuredRoot) return resolve(configuredRoot, relativePath);
  const cwd = process.cwd();
  const repoRoot = basename(cwd) === "web" && basename(dirname(cwd)) === "apps"
    ? resolve(cwd, "../..")
    : cwd;
  return resolve(repoRoot, relativePath);
}

async function readSnapshot(): Promise<MarketViewSnapshot> {
  const { readFile } = await import("node:fs/promises");
  const { resolve } = await import("node:path");
  const configuredPath = process.env.MARKET_DATA_SNAPSHOT_PATH?.trim();
  const snapshotPath = configuredPath
    ? resolve(configuredPath)
    : await repoDataPath(DEFAULT_SNAPSHOT_PATH);
  const canonical = JSON.parse(
    await readFile(snapshotPath, "utf8"),
  ) as PublicMarketSnapshot;
  return normaliseSnapshot(canonical);
}

export function loadMarketSnapshot(): Promise<MarketViewSnapshot> {
  if (process.env.CARDZ_DATA_MODE?.trim() === "live-db") {
    return import("./live-db-snapshot").then(({ loadLiveDbSnapshot }) => loadLiveDbSnapshot());
  }
  snapshotPromise ??= readSnapshot();
  return snapshotPromise;
}

export async function loadNodeMarketAsset(asset: string): Promise<NodeMarketAsset | null> {
  if (!marketAssetObjectKey(asset)) return null;

  const { readFile } = await import("node:fs/promises");
  try {
    const contents = await readFile(await repoDataPath(`${MARKET_ASSETS_PATH}/${asset}`));
    return {
      body: contents.buffer.slice(
        contents.byteOffset,
        contents.byteOffset + contents.byteLength,
      ) as ArrayBuffer,
      // Live engineering image requests must never rebuild the full DB-backed
      // market snapshot merely to populate a diagnostic response header.
      generation: process.env.CARDZ_DATA_MODE?.trim() === "live-db"
        ? "db3308-live"
        : (await loadMarketSnapshot()).generation,
    };
  } catch {
    return null;
  }
}

export interface ScopeOptions {
  page?: number;
  pageSize?: number;
}

export function scopeSnapshot(
  snapshot: MarketViewSnapshot,
  scope: "all" | "pokemon" | "one-piece" | "watchlist",
  options?: ScopeOptions,
): MarketViewSnapshot {
  const canonical = canonicalCards(snapshot);
  if (scope === "all") {
    const cards = canonical
      .filter((card) => card.marketRank >= 1 && card.marketRank <= 100)
      .map((card) => ({ ...card, rank: card.marketRank, viewRank: card.marketRank }))
      .map(listCard);
    return { ...snapshot, coverage: scopedCoverage(cards.length), top100: cards, watchlist: [] };
  }
  if (scope === "watchlist") {
    const all = canonical.filter((card) => card.marketRank >= 101);
    const pageSize = Math.min(Math.max(Math.trunc(options?.pageSize ?? 200), 1), 500);
    const pageCount = Math.max(Math.ceil(all.length / pageSize), 1);
    const page = Math.min(Math.max(Math.trunc(options?.page ?? 1), 1), pageCount);
    const cards = all
      .slice((page - 1) * pageSize, page * pageSize)
      .map((card) => ({ ...card, rank: card.marketRank, viewRank: card.marketRank }))
      .map(listCard);
    return { ...snapshot, coverage: scopedCoverage(cards.length, all.length), top100: cards, watchlist: [] };
  }
  const expected = scope === "pokemon" ? "Pokémon" : "One Piece";
  const cards = gameView(canonical, expected).map(listCard);
  return { ...snapshot, coverage: scopedCoverage(cards.length), top100: cards, watchlist: [] };
}

export function singleCardSnapshot(snapshot: MarketViewSnapshot, id: string): MarketViewSnapshot {
  const card = [...snapshot.top100, ...snapshot.watchlist]
    .find((candidate) => candidate.id === id);
  const cards = card ? [card] : [];
  return { ...snapshot, coverage: scopedCoverage(cards.length), top100: cards, watchlist: [] };
}
