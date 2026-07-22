export const MARKET_ASSET_CACHE_CONTROL = "public, max-age=300, s-maxage=3600, stale-while-revalidate=60";

const marketAssetPattern = /^[a-f0-9]{64}\.webp$/;

export function marketAssetObjectKey(asset: string): string | null {
  return marketAssetPattern.test(asset) ? `market-assets/${asset}` : null;
}

export function marketAssetHash(asset: string): string | null {
  return marketAssetPattern.test(asset) ? asset.slice(0, 64) : null;
}
