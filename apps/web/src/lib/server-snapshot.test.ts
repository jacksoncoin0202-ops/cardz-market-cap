import { createHash } from "node:crypto";
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { PublicMarketSnapshot } from "@cardz/market-data";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cloudflareEnv } from "./cloudflare-env";
import { loadMarketSnapshot, loadNodeMarketAsset, scopeSnapshot } from "./server-snapshot";

vi.mock("./cloudflare-env", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./cloudflare-env")>();
  return {
    ...actual,
    cloudflareEnv: vi.fn(actual.cloudflareEnv),
  };
});

const seedPath = fileURLToPath(new URL("../../../../data/public/seed-snapshot.json", import.meta.url));
const assetsPath = fileURLToPath(new URL("../../../../data/public/market-assets/", import.meta.url));
const temporaryRoots: string[] = [];
const testDbQc = {
  runId: "daily_20260729_fixture",
  receiptSha256: "1".repeat(64),
  database: "cardz_market_cap" as const,
  universeCandidateSha256: "2".repeat(64),
};

function sortObject(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortObject);
  if (value && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value)
        .sort(([left], [right]) => left.localeCompare(right))
        .map(([key, entry]) => [key, sortObject(entry)]),
    );
  }
  return value;
}

function receiptSnapshotContentSha256(snapshot: PublicMarketSnapshot): string {
  const normalized = structuredClone(snapshot);
  normalized.generation.contentSha256 = "";
  normalized.generation.qcReceiptSha256 = "";
  return createHash("sha256")
    .update(JSON.stringify(sortObject(normalized)))
    .digest("hex");
}

function qcReceiptPayload(snapshot: PublicMarketSnapshot): string {
  const production = snapshot.generation.mode === "production"
    && snapshot.generation.productionEligible;
  const cards = [...snapshot.top100, ...snapshot.watchlist].map((card) => {
    const media = {
      base: {
        key: `market-assets/${card.image.sha256}.webp`,
        sha256: card.image.sha256,
        width: card.image.width,
        height: card.image.height,
      },
      "200": {
        key: `market-assets/${card.image.sha256}_200.webp`,
        sha256: "b".repeat(64),
        width: 200,
        height: 280,
      },
      "600": {
        key: `market-assets/${card.image.sha256}_600.webp`,
        sha256: "c".repeat(64),
        width: 429,
        height: 600,
      },
    };
    return {
      id: card.id,
      imageSha256: card.image.sha256,
      decision: "passed",
      evidenceSha256: "e".repeat(64),
      ...(production ? { media } : {}),
    };
  });
  const mediaAssets = cards.flatMap((card) => {
    if (!("media" in card) || card.media === undefined) return [];
    return Object.entries(card.media).map(([variant, asset]) => ({
      ...asset,
      variant,
      baseSha256: card.imageSha256,
    }));
  });
  return `${JSON.stringify({
    schemaVersion: 1,
    ...(production
      ? {
          runId: testDbQc.runId,
          dbQc: testDbQc,
          dbQcReceiptSha256: testDbQc.receiptSha256,
          universeCandidateSha256: testDbQc.universeCandidateSha256,
          snapshotContentSha256: receiptSnapshotContentSha256(snapshot),
        }
      : {}),
    generationId: snapshot.generation.id,
    checkedAt: snapshot.generation.effectiveAt,
    status: "passed",
    claim: snapshot.top100.length === 100 ? "verified-top-100" : "verified-top-n",
    requestedCount: 100,
    verifiedCount: snapshot.top100.length,
    cards,
    ...(production ? { media: { prefix: "market-assets/", assets: mediaAssets } } : {}),
    blockers: [],
  })}\n`;
}

function bindReceipt(
  source: PublicMarketSnapshot,
  receiptPayload: string,
): PublicMarketSnapshot {
  const snapshot = structuredClone(source);
  snapshot.generation.qcReceiptSha256 = createHash("sha256")
    .update(receiptPayload)
    .digest("hex");
  snapshot.generation.contentSha256 = "";
  snapshot.generation.contentSha256 = createHash("sha256")
    .update(JSON.stringify(sortObject(snapshot)))
    .digest("hex");
  return snapshot;
}

function withGeneration(
  source: PublicMarketSnapshot,
  generationId: string,
  production = false,
): PublicMarketSnapshot {
  const snapshot = structuredClone(source);
  snapshot.generation.id = generationId;
  if (production) {
    snapshot.generation.mode = "production";
    snapshot.generation.productionEligible = true;
    snapshot.generation.blockers = [];
    snapshot.generation.dbQc = testDbQc;
  }
  snapshot.top100 = snapshot.top100.slice(0, 1);
  snapshot.watchlist = [];
  snapshot.coverage.claim = snapshot.top100.length === 100 ? "verified-top-100" : "verified-top-n";
  snapshot.coverage.requestedCount = 100;
  snapshot.coverage.verifiedCount = snapshot.top100.length;
  snapshot.coverage.top100Count = snapshot.top100.length;
  snapshot.coverage.watchlistCount = snapshot.watchlist.length;
  snapshot.top100.forEach((card, index) => {
    card.marketRank ||= card.rank;
    card.viewRank = index + 1;
    card.rank = card.viewRank;
  });
  snapshot.watchlist.forEach((card, index) => {
    card.marketRank ||= card.rank;
    card.viewRank = index + 101;
    card.rank = card.viewRank;
  });
  return bindReceipt(snapshot, qcReceiptPayload(snapshot));
}

/*
 * relaxed-launch-v1 規模 fixture：top100 原封不動（正好 100 張），watchlist 以
 * seed 卡做模板複製出 101–546 位共 446 張（全 snapshot ~546 張）。每 5 張一張
 * 完全冇 30 日成交（values null、coverage unavailable）——呢啲卡要誠實通過
 * normalise，唔准變假零、唔准 crash。
 */
function withInflatedGeneration(source: PublicMarketSnapshot, generationId: string): PublicMarketSnapshot {
  const snapshot = structuredClone(source);
  snapshot.generation.id = generationId;
  const template = snapshot.watchlist[0];
  snapshot.watchlist = Array.from({ length: 446 }, (_, index) => {
    const rank = index + 101;
    const card = structuredClone(template);
    card.id = `inflated_${rank}`;
    card.marketRank = rank;
    card.viewRank = rank;
    card.rank = rank;
    if (rank % 5 === 0) {
      for (const window of ["1d", "7d", "30d"] as const) {
        card.windows[window].trackedSales = {
          valueUsd: { value: null, status: "unavailable", asOf: null },
          count: { value: null, status: "unavailable", asOf: null },
          coverage: "unavailable",
          asOf: null,
        };
      }
      card.historyDaily = card.historyDaily.map((point) => ({
        ...point,
        trackedSalesValueUsd: null,
        trackedSalesCount: null,
        salesCoverage: "unavailable",
      }));
    }
    return card;
  });
  snapshot.coverage.top100Count = snapshot.top100.length;
  snapshot.coverage.watchlistCount = snapshot.watchlist.length;
  return bindReceipt(snapshot, qcReceiptPayload(snapshot));
}

async function writeGeneration(
  root: string,
  snapshot: PublicMarketSnapshot,
  receiptPayload = qcReceiptPayload(snapshot),
): Promise<string> {
  const generationRoot = join(root, "generations", snapshot.generation.id);
  const assetRoot = join(generationRoot, "assets");
  await mkdir(assetRoot, { recursive: true });
  await writeFile(join(generationRoot, "snapshot.json"), `${JSON.stringify(snapshot)}\n`);
  await writeFile(join(generationRoot, "qc-receipt.json"), receiptPayload);
  const firstAsset = basename(snapshot.top100[0].image.src);
  await copyFile(join(assetsPath, firstAsset), join(assetRoot, firstAsset));
  const baseHash = firstAsset.replace(/\.webp$/, "");
  for (const suffix of ["200", "600"]) {
    await copyFile(
      join(assetsPath, `${baseHash}_${suffix}.webp`),
      join(assetRoot, `${baseHash}_${suffix}.webp`),
    );
  }
  const receipt = JSON.parse(receiptPayload) as Record<string, unknown>;
  const localAssets = [
    {
      key: `market-assets/${baseHash}.webp`,
      sha256: baseHash,
      width: snapshot.top100[0].image.width,
      height: snapshot.top100[0].image.height,
      variant: "base",
      baseSha256: baseHash,
    },
    ...await Promise.all(["200", "600"].map(async (suffix) => {
      const name = `${baseHash}_${suffix}.webp`;
      return {
        key: `market-assets/${name}`,
        sha256: createHash("sha256").update(await readFile(join(assetRoot, name))).digest("hex"),
        width: suffix === "200" ? 200 : 429,
        height: suffix === "200" ? 280 : 600,
        variant: suffix,
        baseSha256: baseHash,
      };
    })),
  ];
  const receiptMedia = receipt.media as { assets?: typeof localAssets } | undefined;
  const pointer = {
    schemaVersion: 1,
    generationId: snapshot.generation.id,
    snapshotKey: `generations/${snapshot.generation.id}/snapshot.json`,
    sha256: snapshot.generation.contentSha256,
    qcReceiptKey: `generations/${snapshot.generation.id}/qc-receipt.json`,
    qcReceiptSha256: snapshot.generation.qcReceiptSha256,
    ...(snapshot.generation.dbQc ? { dbQc: snapshot.generation.dbQc } : {}),
    media: {
      prefix: "market-assets/",
      hashes: [baseHash],
      assets: receiptMedia?.assets ?? localAssets,
      remoteVerified: false,
      remoteScope: null,
    },
  };
  await writeFile(join(root, "latest.json"), `${JSON.stringify(pointer)}\n`);
  return firstAsset;
}

afterEach(async () => {
  delete process.env.MARKET_DATA_ALLOW_DEMO;
  delete process.env.MARKET_DATA_POINTER_PATH;
  delete process.env.CARDZ_RUNTIME;
  vi.mocked(cloudflareEnv).mockReset();
  await Promise.all(temporaryRoots.splice(0).map((root) => rm(root, { recursive: true, force: true })));
});

describe("Node versioned snapshot runtime", () => {
  it("reloads an atomic pointer and serves assets from the same generation", async () => {
    const root = await mkdtemp(join(tmpdir(), "cardz-web-generation-"));
    temporaryRoots.push(root);
    const seed = JSON.parse(await readFile(seedPath, "utf8")) as PublicMarketSnapshot;
    const first = withGeneration(seed, "runtime_generation_1");
    const firstAsset = await writeGeneration(root, first);

    process.env.CARDZ_RUNTIME = "node";
    process.env.MARKET_DATA_ALLOW_DEMO = "true";
    process.env.MARKET_DATA_POINTER_PATH = join(root, "latest.json");

    expect((await loadMarketSnapshot()).generation).toBe("runtime_generation_1");
    expect(await loadNodeMarketAsset(firstAsset)).toMatchObject({
      generation: "runtime_generation_1",
    });
    expect(await loadNodeMarketAsset(firstAsset.replace(".webp", "_200.webp"))).toMatchObject({
      generation: "runtime_generation_1",
    });
    expect(await loadNodeMarketAsset(firstAsset.replace(".webp", "_600.webp"))).toMatchObject({
      generation: "runtime_generation_1",
    });

    const second = withGeneration(seed, "runtime_generation_2");
    await writeGeneration(root, second);
    const mismatchedPointer = JSON.parse(await readFile(join(root, "latest.json"), "utf8"));
    mismatchedPointer.qcReceiptSha256 = "f".repeat(64);
    await writeFile(join(root, "latest.json"), `${JSON.stringify(mismatchedPointer)}\n`);
    // 全拆令（owner 2026-08-02）：serve 層唔再攞 pointer.qcReceiptSha256 同 generation
    // 對數，cache key 淨係 generationId + sha256，所以呢個欄位而家係死欄位——
    // 篡改咗都照 serve 新 generation，asset 亦跟住轉。
    expect((await loadMarketSnapshot()).generation).toBe("runtime_generation_2");
    expect(await loadNodeMarketAsset(firstAsset)).toMatchObject({
      generation: "runtime_generation_2",
    });

    await writeGeneration(root, second);
    expect((await loadMarketSnapshot()).generation).toBe("runtime_generation_2");

    await writeFile(join(root, "latest.json"), "{broken");
    expect((await loadMarketSnapshot()).generation).toBe("runtime_generation_2");
  });

  // 全拆令之後 serve 層根本唔開 qc-receipt.json，所以呢三個篡改 fixture 全部照出街。
  // 保留 fixture 原樣：想恢復驗證嘅話，翻轉返下面個斷言就得。
  it("serves generations whose QC receipt is failed or mis-bound", async () => {
    const seed = JSON.parse(await readFile(seedPath, "utf8")) as PublicMarketSnapshot;
    const cases = [
      ["failed status", (receipt: Record<string, unknown>) => {
        receipt.status = "failed";
        receipt.blockers = ["card_failed"];
      }],
      ["missing DB binding", (receipt: Record<string, unknown>) => {
        delete receipt.dbQcReceiptSha256;
      }],
      ["wrong card image", (receipt: Record<string, unknown>) => {
        const cards = receipt.cards as Array<Record<string, unknown>>;
        cards[0].imageSha256 = "f".repeat(64);
      }],
    ] as const;

    for (const [label, mutate] of cases) {
      const root = await mkdtemp(join(tmpdir(), "cardz-web-invalid-receipt-"));
      temporaryRoots.push(root);
      let snapshot = withGeneration(seed, `runtime_invalid_${label.replaceAll(" ", "_")}`, true);
      const receipt = JSON.parse(qcReceiptPayload(snapshot)) as Record<string, unknown>;
      mutate(receipt);
      const receiptPayload = `${JSON.stringify(receipt)}\n`;
      snapshot = bindReceipt(snapshot, receiptPayload);
      await writeGeneration(root, snapshot, receiptPayload);

      process.env.CARDZ_RUNTIME = "node";
      process.env.MARKET_DATA_ALLOW_DEMO = "true";
      process.env.MARKET_DATA_POINTER_PATH = join(root, "latest.json");

      const view = await loadMarketSnapshot();
      expect(view.generation).toBe(`runtime_invalid_${label.replaceAll(" ", "_")}`);
    }
  });

  it("serves a generation whose QC receipt file is absent", async () => {
    // 最直白噉講清楚新契約：serve 層由頭到尾冇 open 過 qc-receipt.json。
    const root = await mkdtemp(join(tmpdir(), "cardz-web-missing-receipt-"));
    temporaryRoots.push(root);
    const seed = JSON.parse(await readFile(seedPath, "utf8")) as PublicMarketSnapshot;
    const snapshot = withGeneration(seed, "runtime_missing_receipt", true);
    await writeGeneration(root, snapshot);
    await rm(join(root, "generations", snapshot.generation.id, "qc-receipt.json"), { force: true });

    process.env.CARDZ_RUNTIME = "node";
    process.env.MARKET_DATA_ALLOW_DEMO = "true";
    process.env.MARKET_DATA_POINTER_PATH = join(root, "latest.json");

    expect((await loadMarketSnapshot()).generation).toBe("runtime_missing_receipt");
  });

  // Re-seal 偵測（receipt.snapshotContentSha256 vs 重算 digest）一齊拆咗，
  // 所以出 receipt 之後改嘅 metric 而家會一路去到 render 出嚟嘅 view。
  it("serves resealed card metrics because the receipt is no longer verified", async () => {
    const root = await mkdtemp(join(tmpdir(), "cardz-web-resealed-snapshot-"));
    temporaryRoots.push(root);
    const seed = JSON.parse(await readFile(seedPath, "utf8")) as PublicMarketSnapshot;
    const snapshot = withGeneration(seed, "runtime_resealed_metrics", true);
    const receiptPayload = qcReceiptPayload(snapshot);
    const card = snapshot.top100[0];
    if (
      typeof card.pricePsa10.value !== "number"
      || typeof card.populationPsa10.value !== "number"
    ) {
      throw new Error("test fixture requires ready price and population");
    }
    card.pricePsa10.value += 1;
    card.marketCap.value = card.pricePsa10.value * card.populationPsa10.value;
    snapshot.generation.contentSha256 = "";
    snapshot.generation.contentSha256 = createHash("sha256")
      .update(JSON.stringify(sortObject(snapshot)))
      .digest("hex");
    await writeGeneration(root, snapshot, receiptPayload);

    process.env.CARDZ_RUNTIME = "node";
    process.env.MARKET_DATA_ALLOW_DEMO = "true";
    process.env.MARKET_DATA_POINTER_PATH = join(root, "latest.json");

    const view = await loadMarketSnapshot();
    expect(view.generation).toBe("runtime_resealed_metrics");
    expect(view.top100[0].pricePsa10.value).toBe(card.pricePsa10.value);
    expect(view.top100[0].marketCap.value).toBe(card.marketCap.value);
  });

  it("allows a production local canary pointer without remote verification", async () => {
    const root = await mkdtemp(join(tmpdir(), "cardz-web-local-canary-"));
    temporaryRoots.push(root);
    const seed = JSON.parse(await readFile(seedPath, "utf8")) as PublicMarketSnapshot;
    const snapshot = withGeneration(seed, "runtime_local_production", true);
    await writeGeneration(root, snapshot);

    process.env.CARDZ_RUNTIME = "node";
    process.env.MARKET_DATA_ALLOW_DEMO = "true";
    process.env.MARKET_DATA_POINTER_PATH = join(root, "latest.json");

    expect((await loadMarketSnapshot()).generation).toBe("runtime_local_production");
  });

  it("serves the full inflated watchlist without the 300-rank ceiling", async () => {
    const root = await mkdtemp(join(tmpdir(), "cardz-web-inflated-"));
    temporaryRoots.push(root);
    const seed = JSON.parse(await readFile(seedPath, "utf8")) as PublicMarketSnapshot;
    const snapshot = withInflatedGeneration(seed, "runtime_inflated_watchlist");
    await writeGeneration(root, snapshot);

    process.env.CARDZ_RUNTIME = "node";
    process.env.MARKET_DATA_ALLOW_DEMO = "true";
    process.env.MARKET_DATA_POINTER_PATH = join(root, "latest.json");

    const view = await loadMarketSnapshot();
    expect(view.generation).toBe("runtime_inflated_watchlist");
    expect(view.top100).toHaveLength(100);
    expect(view.watchlist).toHaveLength(446);

    // watchlist scope = 全部 watchlist 卡，101–546 連續，300/301 之間冇斷層
    const watchlist = scopeSnapshot(view, "watchlist");
    expect(watchlist.top100).toHaveLength(446);
    expect(watchlist.top100.map((card) => card.marketRank)).toEqual(
      Array.from({ length: 446 }, (_, index) => index + 101),
    );
    expect(watchlist.coverage.verifiedCount).toBe(446);

    // top100 scope 不受擴容影響
    expect(scopeSnapshot(view, "all").top100).toHaveLength(100);

    // 冇 30 日成交嘅卡：null 值原樣通過 normalise + scope，冇假零、冇 crash
    const noSales = watchlist.top100.filter(
      (card) => card.windows["30d"].trackedSales.coverage === "unavailable",
    );
    expect(noSales.length).toBeGreaterThan(0);
    for (const card of noSales) {
      expect(card.windows["30d"].trackedSales.valueUsd.value).toBeNull();
      expect(card.windows["30d"].trackedSales.count.value).toBeNull();
    }
  });
});

describe("Cloudflare versioned snapshot runtime", () => {
  // assertRemoteProductionPointer 拆咗，media.remoteVerified 而家係裝飾欄位：
  // true / false 兩種 pointer 都會 serve 同一個 production generation。
  it("serves a production generation regardless of remote verification", async () => {
    const root = await mkdtemp(join(tmpdir(), "cardz-web-cloudflare-"));
    temporaryRoots.push(root);
    const seed = JSON.parse(await readFile(seedPath, "utf8")) as PublicMarketSnapshot;
    const snapshot = withGeneration(seed, "runtime_cloudflare_production", true);
    await writeGeneration(root, snapshot);

    const snapshotKey = `generations/${snapshot.generation.id}/snapshot.json`;
    const receiptKey = `generations/${snapshot.generation.id}/qc-receipt.json`;
    let pointerText = await readFile(join(root, "latest.json"), "utf8");
    const objects = new Map<string, string>([
      [snapshotKey, await readFile(join(root, snapshotKey), "utf8")],
      [receiptKey, await readFile(join(root, receiptKey), "utf8")],
    ]);
    const requestedKeys: string[] = [];
    const bucket = {
      get: async (key: string) => {
        requestedKeys.push(key);
        const value = key === "latest.json" ? pointerText : objects.get(key);
        return value === undefined ? null : { text: async () => value };
      },
    };
    vi.mocked(cloudflareEnv).mockResolvedValue({
      MARKET_DATA: bucket,
      MARKET_DATA_ALLOW_DEMO: "true",
    });

    expect(JSON.parse(pointerText).media.remoteVerified).toBe(false);
    expect((await loadMarketSnapshot()).generation).toBe(snapshot.generation.id);
    expect(requestedKeys).not.toContain(receiptKey);

    const verifiedPointer = JSON.parse(pointerText);
    verifiedPointer.media.remoteVerified = true;
    verifiedPointer.media.remoteScope = "d".repeat(16);
    pointerText = `${JSON.stringify(verifiedPointer)}\n`;

    expect((await loadMarketSnapshot()).generation).toBe(snapshot.generation.id);
  });
});
