import { describe, expect, it } from "vitest";
import { parseSnapshotPointer } from "./snapshot-pointer";

const pointer = {
  schemaVersion: 1,
  generationId: "daily_20260722T031500Z",
  snapshotKey: "generations/daily_20260722T031500Z/snapshot.json",
  sha256: "a".repeat(64),
  media: {
    prefix: "market-assets/" as const,
    hashes: ["b".repeat(64)],
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
  });

  it("rejects missing, duplicate, or malformed media authorization", () => {
    expect(() => parseSnapshotPointer({ ...pointer, media: undefined })).toThrow("Snapshot pointer invalid");
    expect(() => parseSnapshotPointer({ ...pointer, media: { ...pointer.media, hashes: ["b".repeat(64), "b".repeat(64)] } })).toThrow("Snapshot pointer invalid");
    expect(() => parseSnapshotPointer({ ...pointer, media: { ...pointer.media, hashes: ["../private"] } })).toThrow("Snapshot pointer invalid");
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
