import { describe, expect, it } from "vitest";
import { parseSnapshotPointer } from "./snapshot-pointer";

const pointer = {
  schemaVersion: 1,
  generationId: "daily_20260722T031500Z",
  snapshotKey: "generations/daily_20260722T031500Z/snapshot.json",
  sha256: "a".repeat(64),
  qcReceiptKey: "generations/daily_20260722T031500Z/qc-receipt.json",
  qcReceiptSha256: "d".repeat(64),
  media: {
    prefix: "market-assets/" as const,
    hashes: ["b".repeat(64)],
    assets: [
      { key: `market-assets/${"b".repeat(64)}.webp`, sha256: "b".repeat(64) },
      { key: `market-assets/${"b".repeat(64)}_200.webp`, sha256: "e".repeat(64) },
      { key: `market-assets/${"b".repeat(64)}_600.webp`, sha256: "f".repeat(64) },
    ],
    remoteVerified: true,
    remoteScope: "c".repeat(16),
  },
};

describe("snapshot pointer", () => {
  it("accepts the publisher contract", () => {
    expect(parseSnapshotPointer(pointer)).toEqual(pointer);
  });

  it("rejects legacy field names and mismatched generation keys", () => {
    expect(() => parseSnapshotPointer({ key: pointer.snapshotKey, generation: pointer.generationId })).toThrow();
    expect(() => parseSnapshotPointer({ ...pointer, snapshotKey: "generations/other/snapshot.json" })).toThrow();
    expect(() => parseSnapshotPointer({ ...pointer, qcReceiptKey: "generations/other/qc-receipt.json" })).toThrow();
    expect(() => parseSnapshotPointer({ ...pointer, qcReceiptSha256: undefined })).toThrow();
  });

  it("rejects missing, duplicate, or malformed media authorization", () => {
    expect(() => parseSnapshotPointer({ ...pointer, media: undefined })).toThrow("Snapshot pointer invalid");
    expect(() => parseSnapshotPointer({ ...pointer, media: { ...pointer.media, hashes: ["b".repeat(64), "b".repeat(64)] } })).toThrow("Snapshot pointer invalid");
    expect(() => parseSnapshotPointer({ ...pointer, media: { ...pointer.media, hashes: ["../private"] } })).toThrow("Snapshot pointer invalid");
    expect(() => parseSnapshotPointer({ ...pointer, media: { ...pointer.media, assets: pointer.media.assets.slice(0, 2) } })).toThrow("Snapshot pointer invalid");
    expect(() => parseSnapshotPointer({
      ...pointer,
      media: {
        ...pointer.media,
        assets: pointer.media.assets.map((asset, index) => (
          index === 1 ? { ...asset, key: pointer.media.assets[0].key } : asset
        )),
      },
    })).toThrow("Snapshot pointer invalid");
    expect(() => parseSnapshotPointer({
      ...pointer,
      media: { ...pointer.media, remoteVerified: false },
    })).toThrow("Snapshot pointer invalid");
  });

  it("accepts and enforces the relaxed-launch-v1 capacity bounds", () => {
    const pointerWithHashCount = (count: number) => {
      const hashes = Array.from({ length: count }, (_, index) => index.toString(16).padStart(64, "0"));
      return {
        ...pointer,
        media: {
          ...pointer.media,
          hashes,
          assets: hashes.flatMap((hash) => [
            { key: `market-assets/${hash}.webp`, sha256: hash },
            { key: `market-assets/${hash}_200.webp`, sha256: hash },
            { key: `market-assets/${hash}_600.webp`, sha256: hash },
          ]),
        },
      };
    };
    // ~520 卡出街規模：546 hashes / 1638 assets 要照收。
    const launchScale = pointerWithHashCount(546);
    expect(launchScale.media.assets).toHaveLength(1638);
    expect(parseSnapshotPointer(launchScale)).toEqual(launchScale);
    // publicCardsMaximum 1000 係硬上限：1001 拒收。
    expect(() => parseSnapshotPointer(pointerWithHashCount(1001))).toThrow("Snapshot pointer invalid");
  });

  it.each([
    ["dot traversal generation", { ...pointer, generationId: "..", snapshotKey: "generations/../snapshot.json" }],
    ["nested generation", { ...pointer, generationId: "daily/nested", snapshotKey: "generations/daily/nested/snapshot.json" }],
    ["backslash traversal", { ...pointer, generationId: "daily\\..\\private", snapshotKey: "generations/daily\\..\\private/snapshot.json" }],
    ["encoded traversal", { ...pointer, generationId: "%2e%2e", snapshotKey: "generations/%2e%2e/snapshot.json" }],
    ["absolute object key", { ...pointer, snapshotKey: "/generations/daily_20260722T031500Z/snapshot.json" }],
    ["unexpected object name", { ...pointer, snapshotKey: "generations/daily_20260722T031500Z/private.json" }],
    ["overlong generation", { ...pointer, generationId: `d${"a".repeat(128)}`, snapshotKey: `generations/d${"a".repeat(128)}/snapshot.json` }],
  ])("rejects %s", (_label, candidate) => {
    expect(() => parseSnapshotPointer(candidate)).toThrow("Snapshot pointer invalid");
  });
});
