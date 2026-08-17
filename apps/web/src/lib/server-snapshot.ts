import type { PublicMarketSnapshot } from "@cardz/market-data";
import { boxBlockView, boxStory, type BoxSidecarBlock } from "./box-view";
import { marketAssetObjectKey } from "./market-media";
import { normaliseSnapshot } from "./snapshot";
import { currencies, type Currency, type MarketCardView, type MarketMetric, type MarketViewSnapshot, type SealedProductView, type SealedViewBlock } from "./types";

const DEFAULT_SNAPSHOT_PATH = "data/public/seed-snapshot.json";
const BOX_SIDECAR_PATH = "data/public/box-subset.json";
const SEALED_STORIES_PATH = "data/editorial/sealed-stories.json";
const CARD_NAMES_BY_ID_PATH = "data/editorial/card-names-by-id.json";
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

function gameUniverse(cards: MarketCardView[], tcg: "Pokémon" | "One Piece"): MarketCardView[] {
  return cards
    .filter((card) => card.tcg === tcg && card.marketRank > 0)
    .sort((a, b) => a.marketRank - b.marketRank)
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
 * 榜頁再削：唔傳 `name` / `story` / `priceUngradedReference`；window／價 metric
 * 只留顯示要用嘅 `value` / `status` / `asOf` / `sourceSwitched`。
 */
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

function slimMetric(metric: MarketMetric<number>): MarketMetric<number> {
  const slim: MarketMetric<number> = {
    value: metric.value,
    status: metric.status,
    asOf: metric.asOf,
  };
  if (metric.sourceSwitched) slim.sourceSwitched = true;
  return slim;
}

function slimWindows(windows: MarketCardView["windows"]): MarketCardView["windows"] {
  return Object.fromEntries(Object.entries(windows).map(([period, metrics]) => [period, {
    changePct: slimMetric(metrics.changePct),
    marketCapChangePct: slimMetric(metrics.marketCapChangePct),
    trackedSalesChangePct: slimMetric(metrics.trackedSalesChangePct),
    trackedSales: {
      valueUsd: slimMetric(metrics.trackedSales.valueUsd),
      count: slimMetric(metrics.trackedSales.count),
      coverage: metrics.trackedSales.coverage,
      asOf: metrics.trackedSales.asOf,
    },
  }])) as MarketCardView["windows"];
}

function listCard(card: MarketCardView): MarketCardView {
  const { story: _story, priceUngradedReference: _ungraded, ...rest } = card;
  return {
    ...rest,
    historyDaily: [],
    salesSparkline: downsampleSparkline(card.salesSparkline),
    windows: slimWindows(card.windows),
    pricePsa10: slimMetric(card.pricePsa10),
    populationPsa10: slimMetric(card.populationPsa10),
    marketCap: slimMetric(card.marketCap),
  };
}

function listBox(product: SealedProductView): SealedProductView {
  const { story: _story, ...rest } = product;
  return {
    ...rest,
    historyDaily: [],
    salesSparkline: downsampleSparkline(product.salesSparkline),
    windows: Object.fromEntries(Object.entries(product.windows).map(([period, metrics]) => [
      period,
      { changePct: slimMetric(metrics.changePct), soldCount: metrics.soldCount },
    ])) as SealedProductView["windows"],
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

export interface BoxSidecarHealth {
  status: "ok" | "missing" | "degraded";
  asOf: string | null;
  ageHours: number | null;
  error: string | null;
}

let boxSidecarPromise: Promise<SealedViewBlock | undefined> | null = null;
let boxSidecarState: BoxSidecarHealth = { status: "missing", asOf: null, ageHours: null, error: null };

export function boxSidecarHealth(): BoxSidecarHealth {
  const asOf = boxSidecarState.asOf;
  const parsed = asOf ? Date.parse(asOf) : Number.NaN;
  const ageHours = Number.isFinite(parsed) ? Math.round(((Date.now() - parsed) / 36e5) * 10) / 10 : null;
  return { ...boxSidecarState, ageHours };
}

type NameOverlay = Partial<Record<"zhTW" | "zhCN" | "ja" | "ko", string>>;

async function readJsonFile<T>(relativePath: string): Promise<T | null> {
  const { readFile } = await import("node:fs/promises");
  try {
    return JSON.parse(await readFile(await repoDataPath(relativePath), "utf8")) as T;
  } catch {
    return null;
  }
}

async function readSealedStories(): Promise<Record<string, Record<string, string | null>>> {
  const document = await readJsonFile<Record<string, unknown>>(SEALED_STORIES_PATH);
  if (!document) return {};
  return Object.fromEntries(
    Object.entries(document).filter(([key, value]) => key !== "_meta" && value && typeof value === "object"),
  ) as Record<string, Record<string, string | null>>;
}

function overlayBoxStories(
  view: SealedViewBlock | undefined,
  extra: Record<string, Record<string, string | null>>,
): SealedViewBlock | undefined {
  if (!view) return undefined;
  return {
    ...view,
    products: view.products.map((product) => {
      const overlay = boxStory(extra[product.id]);
      if (!overlay) return product;
      const current = product.story;
      return {
        ...product,
        story: {
          en: overlay.en || current?.en || "",
          "zh-TW": overlay["zh-TW"] ?? current?.["zh-TW"] ?? null,
          "zh-CN": overlay["zh-CN"] ?? current?.["zh-CN"] ?? null,
          ja: overlay.ja ?? current?.ja ?? null,
          ko: overlay.ko ?? current?.ko ?? null,
        },
      };
    }),
  };
}

function applyCardNameOverlays(snapshot: MarketViewSnapshot, entries: Record<string, NameOverlay>): MarketViewSnapshot {
  if (!Object.keys(entries).length) return snapshot;
  const paint = (card: MarketCardView): MarketCardView => {
    const entry = entries[card.id];
    if (!entry) return card;
    return {
      ...card,
      name: {
        en: card.officialName ?? card.name?.en ?? "",
        "zh-TW": entry.zhTW?.trim() || null,
        "zh-CN": entry.zhCN?.trim() || null,
        ja: entry.ja?.trim() || null,
        ko: entry.ko?.trim() || null,
      },
    };
  };
  return { ...snapshot, top100: snapshot.top100.map(paint), watchlist: snapshot.watchlist.map(paint) };
}

async function readBoxSidecar(): Promise<SealedViewBlock | undefined> {
  const { readFile } = await import("node:fs/promises");
  try {
    const raw = JSON.parse(await readFile(await repoDataPath(BOX_SIDECAR_PATH), "utf8")) as BoxSidecarBlock;
    const view = overlayBoxStories(boxBlockView(raw), await readSealedStories());
    if (!view) {
      boxSidecarState = { status: "degraded", asOf: raw?.asOf ?? null, ageHours: null, error: "sidecar has no products" };
      return undefined;
    }
    boxSidecarState = { status: "ok", asOf: view.asOf, ageHours: null, error: null };
    return view;
  } catch (error) {
    const code = (error as NodeJS.ErrnoException)?.code;
    boxSidecarState = {
      status: code === "ENOENT" ? "missing" : "degraded",
      asOf: null,
      ageHours: null,
      error: error instanceof Error ? error.message : String(error),
    };
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
  const names = await readJsonFile<{ entries?: Record<string, NameOverlay> }>(CARD_NAMES_BY_ID_PATH);
  return applyCardNameOverlays(await attachBoxSidecar(normaliseSnapshot(canonical)), names?.entries ?? {});
}

export function loadMarketSnapshot(): Promise<MarketViewSnapshot> {
  if (process.env.CARDZ_DATA_MODE?.trim() === "live-db") {
    return import("./live-db-snapshot").then(({ loadLiveDbSnapshot }) =>
      loadLiveDbSnapshot().then(async (snapshot) => {
        const names = await readJsonFile<{ entries?: Record<string, NameOverlay> }>(CARD_NAMES_BY_ID_PATH);
        return applyCardNameOverlays(await attachBoxSidecar(snapshot), names?.entries ?? {});
      }),
    );
  }
  snapshotPromise ??= readSnapshot();
  return snapshotPromise;
}

/* Header 貨幣選單只出有匯率嘅貨幣：baked snapshot 未有某隻貨幣（例如後端未跑新一日 bake）就唔好俾人揀到「暫無資料」。
   snapshot 讀唔到就退返全部（fail-open：header 唔可以因為資料層死而消失）。 */
export async function availableCurrencies(): Promise<Currency[]> {
  try {
    const { rates } = await loadMarketSnapshot();
    const usable = currencies.filter((code) => Number.isFinite(rates[code]) && rates[code] > 0);
    return usable.length > 0 ? usable : [...currencies];
  } catch {
    return [...currencies];
  }
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
    const ranked = canonical
      .filter((card) => card.marketRank >= 1)
      .sort((a, b) => a.marketRank - b.marketRank)
      .map((card) => ({ ...card, rank: card.marketRank, viewRank: card.marketRank }));
    const awaiting = canonical
      .filter((card) => !(card.marketRank >= 1))
      .sort((a, b) => a.id.localeCompare(b.id))
      .map((card) => ({ ...card, rank: 0, viewRank: 0 }));
    const all = [...ranked, ...awaiting];
    const pageSize = Math.min(Math.max(Math.trunc(options?.pageSize ?? 100), 1), 500);
    const pageCount = Math.max(Math.ceil(all.length / pageSize), 1);
    const page = Math.min(Math.max(Math.trunc(options?.page ?? 1), 1), pageCount);
    const cards = all.slice((page - 1) * pageSize, page * pageSize).map(listCard);
    const lead100 = ranked.slice(0, 100).map(listCard);
    return {
      ...withoutSealed(snapshot),
      coverage: scopedCoverage(cards.length, all.length),
      top100: cards,
      watchlist: [],
      lead100,
    };
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
  const ranked = gameUniverse(canonical, expected);
  const pageSize = Math.min(Math.max(Math.trunc(options?.pageSize ?? 100), 1), 500);
  const pageCount = Math.max(Math.ceil(ranked.length / pageSize), 1);
  const page = Math.min(Math.max(Math.trunc(options?.page ?? 1), 1), pageCount);
  const cards = ranked.slice((page - 1) * pageSize, page * pageSize).map(listCard);
  const lead100 = ranked.slice(0, 100).map(listCard);
  return {
    ...withoutSealed(snapshot),
    coverage: scopedCoverage(cards.length, ranked.length),
    top100: cards,
    watchlist: [],
    lead100,
  };
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
