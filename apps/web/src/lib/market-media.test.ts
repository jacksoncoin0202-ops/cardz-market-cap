import { describe, expect, it } from "vitest";
import { MARKET_ASSET_CACHE_CONTROL, marketAssetHash, marketAssetObjectKey } from "./market-media";

describe("market media contract", () => {
  it("accepts only a content-addressed WebP key", () => {
    const hash = "a".repeat(64);
    expect(marketAssetObjectKey(`${hash}.webp`)).toBe(`market-assets/${hash}.webp`);
    expect(marketAssetHash(`${hash}.webp`)).toBe(hash);
  });

  it.each([
    "A".repeat(64) + ".webp",
    "a".repeat(63) + ".webp",
    "a".repeat(64) + ".png",
    "../" + "a".repeat(64) + ".webp",
    "a".repeat(64) + ".webp?download=1",
  ])("rejects unsafe or non-contract asset %s", (asset) => {
    expect(marketAssetObjectKey(asset)).toBeNull();
  });

  it("uses an edge cache that still permits prompt revocation", () => {
    expect(MARKET_ASSET_CACHE_CONTROL).toContain("s-maxage=3600");
    expect(MARKET_ASSET_CACHE_CONTROL).not.toContain("immutable");
  });
});
