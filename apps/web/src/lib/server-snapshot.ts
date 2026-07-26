import { assertPublicSnapshot, type PublicMarketSnapshot } from "@cardz/market-data";
import { cache } from "react";
import { cloudflareEnv, isNodeRuntime } from "./cloudflare-env";
import { getSeedSnapshot, normaliseSnapshot } from "./snapshot";
import { parseSnapshotPointer } from "./snapshot-pointer";
import type { Grader, MarketCardView, MarketViewSnapshot } from "./types";

interface R2Body {
  text(): Promise<string>;
}

interface MarketBucket {
  get(key: string): Promise<R2Body | null>;
}

let runtimeLastGood: MarketViewSnapshot | null = null;

function ranked(cards: MarketCardView[]): MarketCardView[] {
  return [...cards]
    .sort((a, b) => (b.marketCap.value ?? 0) - (a.marketCap.value ?? 0))
    .slice(0, 100)
    .map((card, index) => ({ ...card, rank: index + 1 }));
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
 * Node server（AWS / Docker）路徑：冇 R2 binding，數據來自 build 時打包咗嘅
 * seed snapshot，或者 MARKET_DATA_SNAPSHOT_PATH 指住嘅檔。讀一次 cache 落
 * 進程，換數據要重啟。
 */
let nodeSnapshot: MarketViewSnapshot | null = null;

/*
 * `next build` 會 prerender 靜態頁（/sitemap.xml 等），嗰陣一定行得到呢條路，
 * 而嗰刻 build context 入面通常仲係 demo seed。build 期 throw = build 直接炸，
 * 所以 demo 閘只喺 runtime 生效，build 期放行。
 */
function isBuildPhase(): boolean {
  return process.env.NEXT_PHASE === "phase-production-build";
}

async function loadNodeSnapshot(): Promise<MarketViewSnapshot> {
  if (nodeSnapshot) return nodeSnapshot;
  const snapshotPath = process.env.MARKET_DATA_SNAPSHOT_PATH?.trim();
  if (!snapshotPath) {
    /*
     * 冇 MARKET_DATA_SNAPSHOT_PATH 就用 build 時燒死入 bundle 嗰份。
     * git 入面嗰份 data/public/seed-snapshot.json 按硬規矩永遠係 demo placeholder，
     * 所以 clone → docker build → 出街 呢條路預設會派 360 張假卡，而且 200 OK、
     * 冇 error、冇 log、冇 alert——demo 扮真數據係最壞嘅失敗模式，寧願唔出。
     *
     * normaliseSnapshot() 已經幫我哋計咗：mode === "canonical" 等同
     * generation.mode === "production" && generation.productionEligible。
     */
    const seed = getSeedSnapshot();
    const demoAllowed = process.env.MARKET_DATA_ALLOW_DEMO === "true";
    if (seed.mode !== "canonical" && !demoAllowed && !isBuildPhase()) {
      throw new Error(
        `Refusing to serve non-production data. The snapshot compiled into this build is generation ` +
          `"${seed.generation}" (mode=${seed.mode}); it is a demo placeholder, not real market data. ` +
          `Fix: rebuild the image with a production data/public/seed-snapshot.json in the build context ` +
          `(see docs/AWS_DEPLOY.md "Shipping real data"), or point MARKET_DATA_SNAPSHOT_PATH at a ` +
          `production snapshot file. To serve the demo placeholder deliberately, set MARKET_DATA_ALLOW_DEMO=true.`,
      );
    }
    nodeSnapshot = seed;
    return nodeSnapshot;
  }
  const { readFile } = await import("node:fs/promises");
  const canonical = JSON.parse(await readFile(snapshotPath, "utf8")) as PublicMarketSnapshot;
  assertPublicSnapshot(canonical, { production: process.env.MARKET_DATA_ALLOW_DEMO !== "true" });
  nodeSnapshot = normaliseSnapshot(canonical);
  return nodeSnapshot;
}

export const loadMarketSnapshot = cache(async (): Promise<MarketViewSnapshot> => {
  if (process.env.NODE_ENV === "development") return getSeedSnapshot();
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
    assertPublicSnapshot(canonical, { production: !allowDemo });
    if (canonical.generation.id !== pointer.generationId || canonical.generation.contentSha256 !== pointer.sha256) {
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
});

export function scopeSnapshot(
  snapshot: MarketViewSnapshot,
  scope: "all" | "pokemon" | "one-piece" | "watchlist",
): MarketViewSnapshot {
  if (scope === "all") return { ...snapshot, top100: snapshot.top100.map(listCard), watchlist: [] };
  if (scope === "watchlist") return { ...snapshot, top100: snapshot.watchlist.filter((card) => card.rank >= 101 && card.rank <= 300).map(listCard), watchlist: [] };
  const expected = scope === "pokemon" ? "Pokémon" : "One Piece";
  const candidates = [...snapshot.top100, ...snapshot.watchlist].filter((card) => card.tcg === expected);
  return { ...snapshot, top100: ranked(candidates).map(listCard), watchlist: [] };
}

export function singleCardSnapshot(snapshot: MarketViewSnapshot, id: string): MarketViewSnapshot {
  const card = [...snapshot.top100, ...snapshot.watchlist].find((candidate) => candidate.id === id);
  return { ...snapshot, top100: card ? [card] : [], watchlist: [] };
}

export function graderSnapshot(snapshot: MarketViewSnapshot, grader: Grader): MarketViewSnapshot {
  const cards = [...snapshot.top100, ...snapshot.watchlist]
    .filter((card) => card.graderPopulations[grader].topGradePopulation.value !== null)
    .sort((a, b) => (b.graderPopulations[grader].topGradePopulation.value ?? 0) - (a.graderPopulations[grader].topGradePopulation.value ?? 0))
    .slice(0, 100)
    .map((card, index) => ({ ...card, rank: index + 1 }));
  return { ...snapshot, top100: cards.map(listCard), watchlist: [] };
}
