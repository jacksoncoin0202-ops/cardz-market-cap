export const DEFAULT_SNAPSHOT_STALE_AFTER_SECONDS = 48 * 60 * 60;

export interface SnapshotFreshness {
  checkedAt: string;
  snapshotAgeSeconds: number | null;
  snapshotStale: boolean;
  snapshotStaleAfterSeconds: number;
}

export function snapshotStaleAfterSeconds(raw: string | undefined): number {
  if (!raw || !/^\d+$/.test(raw)) return DEFAULT_SNAPSHOT_STALE_AFTER_SECONDS;
  const value = Number(raw);
  return Number.isSafeInteger(value) && value >= 60 ? value : DEFAULT_SNAPSHOT_STALE_AFTER_SECONDS;
}

export function snapshotFreshness(
  effectiveAt: string,
  nowMs = Date.now(),
  staleAfterSeconds = DEFAULT_SNAPSHOT_STALE_AFTER_SECONDS,
): SnapshotFreshness {
  const checkedAt = new Date(nowMs).toISOString();
  const effectiveMs = Date.parse(effectiveAt);
  if (!Number.isFinite(effectiveMs)) {
    return {
      checkedAt,
      snapshotAgeSeconds: null,
      snapshotStale: true,
      snapshotStaleAfterSeconds: staleAfterSeconds,
    };
  }
  const snapshotAgeSeconds = Math.max(0, Math.floor((nowMs - effectiveMs) / 1000));
  return {
    checkedAt,
    snapshotAgeSeconds,
    snapshotStale: snapshotAgeSeconds > staleAfterSeconds,
    snapshotStaleAfterSeconds: staleAfterSeconds,
  };
}
