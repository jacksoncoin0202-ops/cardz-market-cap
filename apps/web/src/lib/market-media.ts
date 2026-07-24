/* content-hash 命名：內容永遠唔變，可以 immutable 長 cache（用戶 2026-07-24 性能要求） */
export const MARKET_ASSET_CACHE_CONTROL = "public, max-age=31536000, immutable";

const marketAssetPattern = /^[a-f0-9]{64}(?:_(?:200|600))?\.webp$/;

export function marketAssetObjectKey(asset: string): string | null {
  return marketAssetPattern.test(asset) ? `market-assets/${asset}` : null;
}

export function marketAssetHash(asset: string): string | null {
  const match = asset.match(/^([a-f0-9]{64})(?:_(?:200|600))?\.webp$/);
  return match ? match[1] : null;
}
