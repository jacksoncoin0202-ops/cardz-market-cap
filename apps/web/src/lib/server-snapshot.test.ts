import { createHash } from "node:crypto";
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { basename, join } from "node:path";
import { fileURLToPath } from "node:url";
import type { PublicMarketSnapshot } from "@cardz/market-data";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cloudflareEnv } from "./cloudflare-env";
import { loadMarketSnapshot, loadNodeMarketAsset } from "./server-snapshot";

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
    expect((await loadMarketSnapshot()).generation).toBe("runtime_generation_1");

    await writeGeneration(root, second);
    expect((await loadMarketSnapshot()).generation).toBe("runtime_generation_2");

    await writeFile(join(root, "latest.json"), "{broken");
    expect((await loadMarketSnapshot()).generation).toBe("runtime_generation_2");
  });

  it("rejects hash-bound receipts with invalid status or card binding", async () => {
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

      await expect(loadMarketSnapshot()).rejects.toThrow(/QC receipt/);
    }
  });

  it("rejects resealed card metrics when the QC receipt is reused", async () => {
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

    await expect(loadMarketSnapshot()).rejects.toThrow(
      "QC receipt DB/final snapshot binding is invalid",
    );
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
});

describe("Cloudflare versioned snapshot runtime", () => {
  it("requires remote verification before serving a production generation", async () => {
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
    const bucket = {
      get: async (key: string) => {
        const value = key === "latest.json" ? pointerText : objects.get(key);
        return value === undefined ? null : { text: async () => value };
      },
    };
    vi.mocked(cloudflareEnv).mockResolvedValue({
      MARKET_DATA: bucket,
      MARKET_DATA_ALLOW_DEMO: "true",
    });

    expect((await loadMarketSnapshot()).generation).not.toBe(snapshot.generation.id);

    const verifiedPointer = JSON.parse(pointerText);
    verifiedPointer.media.remoteVerified = true;
    verifiedPointer.media.remoteScope = "d".repeat(16);
    pointerText = `${JSON.stringify(verifiedPointer)}\n`;

    expect((await loadMarketSnapshot()).generation).toBe(snapshot.generation.id);
  });
});
