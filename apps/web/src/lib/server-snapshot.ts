import type { PublicMarketSnapshot } from "@cardz/market-data";
import { boxBlockView, type BoxSidecarBlock } from "./box-view";
import { marketAssetObjectKey } from "./market-media";
import { normaliseSnapshot } from "./snapshot";
import type { LocalizedText, MarketCardView, MarketMetric, MarketViewSnapshot, SealedProductView, SealedViewBlock } from "./types";

const DEFAULT_SNAPSHOT_PATH = "data/public/seed-snapshot.json";
const BOX_SIDECAR_PATH = "data/public/box-subset.json";
const MARKET_ASSETS_PATH = "data/public/market-assets";

export interface NodeMarketAsset {
  body: ArrayBuffer;
  generation: string;
}

let snapshotPromise: Promise<MarketViewSnapshot> | null = null;

function canonicalCards(snapshot: MarketViewSnapshot): MarketCardView[] {
  return [...snapshot.top100, ...snapshot.watchlist]
    .sort((a, b) => {
      const rankA = a.marketRank > 0 ? a.marketRank : Number.MAX_SAFE_INTEGER;
      const rankB = b.marketRank > 0 ? b.marketRank : Number.MAX_SAFE_INTEGER;
      return rankA - rankB || a.id.localeCompare(b.id);
    });
}

function gameView(cards: MarketCardView[], tcg: "Pokémon" | "One Piece"): MarketCardView[] {
  return cards
    .filter((card) => card.tcg === tcg && card.marketRank > 0)
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

/*
 * 榜頁投影。榜頁冇任何組件讀 `historyDaily`（唯一嘅圖 <Sparkline> 讀
 * `salesSparkline`），但佢實測佔榜頁 card bytes 約 89%（seed snapshot：頭 100 張
 * 7.27MB 入面 6.53MB 係佢），所以榜頁一律清空。
 *
 * `story` 同一道理，2026-08-11 一齊清：live `/api/v1/market?scope=all` 753,285
 * bytes 入面佢佔 222,959（29.6%），watchlist 1,427,488 入面佔 397,846（27.9%），
 * 而榜頁一個讀者都冇 —— 全 app 得兩處讀 `card.story`（app/card/[id]/page.tsx:27、
 * components/card-detail.tsx:32），兩處都喺詳情頁。詳情頁行 `singleCardSnapshot`，
 * 唔會經呢度，仍然攞到完整歷史同故事。
 *
 * 注意 `story` 喺 types.ts 係必填 `LocalizedText`，所以要清空唔係刪 key——
 * 出一個共用 frozen 空值，contract 唔郁，client 讀 `card.story[locale]` 唔會炸。
 */
const EMPTY_STORY: LocalizedText = Object.freeze({
  en: "",
  "zh-TW": "",
  "zh-CN": "",
  ja: "",
  ko: "",
});

const EMPTY_METRIC: MarketMetric<number> = Object.freeze({
  value: null,
  status: "unavailable",
  asOf: null,
});

const LIST_SPARKLINE_POINTS = 60;

function downsampleSparkline(values: number[] | undefined, maxPoints = LIST_SPARKLINE_POINTS): number[] {
  if (!values?.length) return [];
  if (values.length <= maxPoints) return values;
  const last = maxPoints - 1;
  return Array.from({ length: maxPoints }, (_, index) => {
    const source = index === last
      ? values.length - 1
      : Math.round((index * (values.length - 1)) / last);
    return values[source];
  });
}

function listCard(card: MarketCardView): MarketCardView {
  return {
    ...card,
    story: EMPTY_STORY,
    historyDaily: [],
    priceUngradedReference: EMPTY_METRIC,
    salesSparkline: downsampleSparkline(card.salesSparkline),
  };
}

function listBox(product: SealedProductView): SealedProductView {
  return {
    ...product,
    story: null,
    historyDaily: [],
    salesSparkline: downsampleSparkline(product.salesSparkline),
  };
}

function withoutCards(snapshot: MarketViewSnapshot, sealed?: SealedViewBlock): MarketViewSnapshot {
  return {
    ...snapshot,
    top100: [],
    watchlist: [],
    sealed,
  };
}

function withoutSealed(snapshot: MarketViewSnapshot): MarketViewSnapshot {
  const { sealed: _sealed, ...rest } = snapshot;
  return rest;
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

let boxSidecarPromise: Promise<SealedViewBlock | undefined> | null = null;

async function readBoxSidecar(): Promise<SealedViewBlock | undefined> {
  const { readFile } = await import("node:fs/promises");
  try {
    const raw = JSON.parse(await readFile(await repoDataPath(BOX_SIDECAR_PATH), "utf8")) as BoxSidecarBlock;
    return boxBlockView(raw);
  } catch {
    return undefined;
  }
}

function loadBoxSidecar(): Promise<SealedViewBlock | undefined> {
  boxSidecarPromise ??= readBoxSidecar();
  return boxSidecarPromise;
}

async function attachBoxSidecar(snapshot: MarketViewSnapshot): Promise<MarketViewSnapshot> {
  const sealed = await loadBoxSidecar();
  if (!sealed) return snapshot;
  return { ...snapshot, sealed };
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
  return attachBoxSidecar(normaliseSnapshot(canonical));
}

export function loadMarketSnapshot(): Promise<MarketViewSnapshot> {
  if (process.env.CARDZ_DATA_MODE?.trim() === "live-db") {
    return import("./live-db-snapshot").then(({ loadLiveDbSnapshot }) =>
      loadLiveDbSnapshot().then(attachBoxSidecar),
    );
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
    return { ...withoutSealed(snapshot), coverage: scopedCoverage(cards.length), top100: cards, watchlist: [] };
  }
  if (scope === "watchlist") {
    /*
     * Owner 政策（2026-08-12 覆蓋 8/11 上限）：watchlist 放 rank 101+，
     * 沿用現有 pagination；rank 0（awaiting fresh price）放最後一頁之後的
     * 獨立區段，唔顯示假排名。卡頁 URL / FE03 layout / GEO 不變。
     */
    const ranked = canonical
      .filter((card) => card.marketRank >= 101)
      .sort((a, b) => a.marketRank - b.marketRank);
    const awaiting = canonical
      .filter((card) => !(card.marketRank >= 1))
      .sort((a, b) => a.id.localeCompare(b.id));
    const all = [
      ...ranked.map((card) => ({ ...card, rank: card.marketRank, viewRank: card.marketRank })),
      ...awaiting.map((card) => ({ ...card, rank: 0, viewRank: 0 })),
    ];
    const pageSize = Math.min(Math.max(Math.trunc(options?.pageSize ?? 200), 1), 500);
    const pageCount = Math.max(Math.ceil(all.length / pageSize), 1);
    const page = Math.min(Math.max(Math.trunc(options?.page ?? 1), 1), pageCount);
    const cards = all
      .slice((page - 1) * pageSize, page * pageSize)
      .map(listCard);
    return { ...withoutSealed(snapshot), coverage: scopedCoverage(cards.length, all.length), top100: cards, watchlist: [] };
  }
  const expected = scope === "pokemon" ? "Pokémon" : "One Piece";
  const cards = gameView(canonical, expected).map(listCard);
  return { ...withoutSealed(snapshot), coverage: scopedCoverage(cards.length), top100: cards, watchlist: [] };
}

export function singleCardSnapshot(snapshot: MarketViewSnapshot, id: string): MarketViewSnapshot {
  const card = [...snapshot.top100, ...snapshot.watchlist]
    .find((candidate) => candidate.id === id);
  const cards = card ? [card] : [];
  return { ...withoutSealed(snapshot), coverage: scopedCoverage(cards.length), top100: cards, watchlist: [] };
}

export function boxListSnapshot(snapshot: MarketViewSnapshot): MarketViewSnapshot {
  return withoutCards(
    snapshot,
    snapshot.sealed
      ? { ...snapshot.sealed, products: snapshot.sealed.products.map(listBox) }
      : undefined,
  );
}

export function boxDetailSnapshot(snapshot: MarketViewSnapshot, id: string): MarketViewSnapshot {
  const product = snapshot.sealed?.products.find((candidate) => candidate.id === id);
  return withoutCards(
    snapshot,
    snapshot.sealed
      ? { ...snapshot.sealed, products: product ? [product] : [] }
      : undefined,
  );
}
