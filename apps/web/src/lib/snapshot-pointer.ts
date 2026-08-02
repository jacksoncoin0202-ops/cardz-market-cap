export interface SnapshotPointer {
  schemaVersion: 1;
  generationId: string;
  snapshotKey: string;
  sha256: string;
  qcReceiptKey: string;
  qcReceiptSha256: string;
  media: {
    prefix: "market-assets/";
    hashes: string[];
    assets: Array<{
      key: string;
      sha256: string;
    }>;
    remoteVerified: boolean;
    remoteScope: string | null;
  };
}

const GENERATION_ID_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$/;
const SNAPSHOT_KEY_PATTERN = /^generations\/([A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?)\/snapshot\.json$/;
const QC_RECEIPT_KEY_PATTERN = /^generations\/([A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?)\/qc-receipt\.json$/;
const MEDIA_ASSET_KEY_PATTERN = /^market-assets\/([a-f0-9]{64})(?:_(200|600))?\.webp$/;
const SHA256_PATTERN = /^[a-f0-9]{64}$/;

export function parseSnapshotPointer(value: unknown): SnapshotPointer {
  if (!value || typeof value !== "object") throw new Error("Snapshot pointer invalid");
  const pointer = value as Partial<SnapshotPointer>;
  const keyMatch = typeof pointer.snapshotKey === "string" ? pointer.snapshotKey.match(SNAPSHOT_KEY_PATTERN) : null;
  const receiptKeyMatch = typeof pointer.qcReceiptKey === "string"
    ? pointer.qcReceiptKey.match(QC_RECEIPT_KEY_PATTERN)
    : null;
  const hashes = pointer.media?.hashes;
  const assets = pointer.media?.assets;
  const assetKeys = Array.isArray(assets)
    ? assets.map((asset) => asset?.key)
    : [];
  const expectedAssetKeys = Array.isArray(hashes)
    ? hashes.flatMap((hash) => [
        `market-assets/${hash}.webp`,
        `market-assets/${hash}_200.webp`,
        `market-assets/${hash}_600.webp`,
      ])
    : [];
  if (
    pointer.schemaVersion !== 1 ||
    typeof pointer.generationId !== "string" ||
    !GENERATION_ID_PATTERN.test(pointer.generationId) ||
    keyMatch?.[1] !== pointer.generationId ||
    receiptKeyMatch?.[1] !== pointer.generationId ||
    typeof pointer.sha256 !== "string" ||
    !SHA256_PATTERN.test(pointer.sha256) ||
    typeof pointer.qcReceiptSha256 !== "string" ||
    !SHA256_PATTERN.test(pointer.qcReceiptSha256) ||
    pointer.media?.prefix !== "market-assets/" ||
    !Array.isArray(hashes) ||
    hashes.length === 0 ||
    hashes.length > 1000 ||
    new Set(hashes).size !== hashes.length ||
    hashes.some((hash) => typeof hash !== "string" || !SHA256_PATTERN.test(hash)) ||
    !Array.isArray(assets) ||
    assets.length !== expectedAssetKeys.length ||
    assets.length > 3000 ||
    new Set(assetKeys).size !== assets.length ||
    assets.some((asset) => {
      if (!asset || typeof asset !== "object") return true;
      const assetMatch = typeof asset.key === "string"
        ? asset.key.match(MEDIA_ASSET_KEY_PATTERN)
        : null;
      return (
        !assetMatch ||
        typeof asset.sha256 !== "string" ||
        !SHA256_PATTERN.test(asset.sha256) ||
        (assetMatch[2] === undefined && asset.sha256 !== assetMatch[1])
      );
    }) ||
    expectedAssetKeys.some((key) => !assetKeys.includes(key)) ||
    typeof pointer.media?.remoteVerified !== "boolean" ||
    (pointer.media.remoteVerified
      ? !/^[a-f0-9]{16}$/.test(pointer.media.remoteScope ?? "")
      : pointer.media.remoteScope !== null)
  ) {
    throw new Error("Snapshot pointer invalid");
  }
  return pointer as SnapshotPointer;
}
