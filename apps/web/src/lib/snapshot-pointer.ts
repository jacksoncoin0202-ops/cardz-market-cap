export interface SnapshotPointer {
  schemaVersion: 1;
  generationId: string;
  snapshotKey: string;
  sha256: string;
  media: {
    prefix: "market-assets/";
    hashes: string[];
    remoteVerified: boolean;
    remoteScope: string | null;
  };
}

const GENERATION_ID_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?$/;
const SNAPSHOT_KEY_PATTERN = /^generations\/([A-Za-z0-9](?:[A-Za-z0-9_-]{0,126}[A-Za-z0-9])?)\/snapshot\.json$/;

export function parseSnapshotPointer(value: unknown): SnapshotPointer {
  if (!value || typeof value !== "object") throw new Error("Snapshot pointer invalid");
  const pointer = value as Partial<SnapshotPointer>;
  const keyMatch = typeof pointer.snapshotKey === "string" ? pointer.snapshotKey.match(SNAPSHOT_KEY_PATTERN) : null;
  const hashes = pointer.media?.hashes;
  if (
    pointer.schemaVersion !== 1 ||
    typeof pointer.generationId !== "string" ||
    !GENERATION_ID_PATTERN.test(pointer.generationId) ||
    keyMatch?.[1] !== pointer.generationId ||
    typeof pointer.sha256 !== "string" ||
    !/^[a-f0-9]{64}$/.test(pointer.sha256) ||
    pointer.media?.prefix !== "market-assets/" ||
    !Array.isArray(hashes) ||
    hashes.length === 0 ||
    hashes.length > 500 ||
    new Set(hashes).size !== hashes.length ||
    hashes.some((hash) => typeof hash !== "string" || !/^[a-f0-9]{64}$/.test(hash)) ||
    typeof pointer.media?.remoteVerified !== "boolean" ||
    (pointer.media?.remoteScope !== null && !/^[a-f0-9]{16}$/.test(pointer.media?.remoteScope ?? ""))
  ) {
    throw new Error("Snapshot pointer invalid");
  }
  return pointer as SnapshotPointer;
}
