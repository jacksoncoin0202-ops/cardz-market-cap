import { describe, expect, it } from "vitest";
import { MARKET_ASSET_CACHE_CONTROL, marketAssetHash, marketAssetObjectKey } from "./market-media";

describe("market media contract", () => {
  it("accepts only a content-addressed WebP key", () => {
    const hash = "a".repeat(64);
    expect(marketAssetObjectKey(`${hash}.webp`)).toBe(`market-assets/${hash}.webp`);
    expect(marketAssetHash(`${hash}.webp`)).toBe(hash);
  });

  it("accepts derivative variants of a content-addressed WebP key", () => {
    const hash = "b".repeat(64);
    expect(marketAssetObjectKey(`${hash}_200.webp`)).toBe(`market-assets/${hash}_200.webp`);
    expect(marketAssetObjectKey(`${hash}_600.webp`)).toBe(`market-assets/${hash}_600.webp`);
    expect(marketAssetHash(`${hash}_200.webp`)).toBe(hash);
    expect(marketAssetHash(`${hash}_600.webp`)).toBe(hash);
  });

  it.each([
    "A".repeat(64) + ".webp",
    "a".repeat(63) + ".webp",
    "a".repeat(64) + ".png",
    "../" + "a".repeat(64) + ".webp",
    "a".repeat(64) + ".webp?download=1",
    "a".repeat(64) + "_999.webp",
  ])("rejects unsafe or non-contract asset %s", (asset) => {
    expect(marketAssetObjectKey(asset)).toBeNull();
  });

  it("uses an immutable edge cache for content-addressed assets", () => {
    expect(MARKET_ASSET_CACHE_CONTROL).toContain("max-age=31536000");
    expect(MARKET_ASSET_CACHE_CONTROL).toContain("immutable");
  });
});
