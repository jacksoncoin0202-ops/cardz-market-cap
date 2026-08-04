import { describe, expect, it } from "vitest";
import {
  DEFAULT_SNAPSHOT_STALE_AFTER_SECONDS,
  snapshotFreshness,
  snapshotStaleAfterSeconds,
} from "./snapshot-health";

describe("snapshot health", () => {
  const now = Date.parse("2026-07-28T12:00:00.000Z");

  it("reports age and keeps a fresh last-known-good generation ready", () => {
    expect(snapshotFreshness("2026-07-28T11:00:00.000Z", now, 7200)).toEqual({
      checkedAt: "2026-07-28T12:00:00.000Z",
      snapshotAgeSeconds: 3600,
      snapshotStale: false,
      snapshotStaleAfterSeconds: 7200,
    });
  });

  it("marks old or invalid effective times stale without inventing an age", () => {
    expect(snapshotFreshness("2026-07-25T12:00:00.000Z", now).snapshotStale).toBe(true);
    expect(snapshotFreshness("not-a-date", now)).toMatchObject({
      snapshotAgeSeconds: null,
      snapshotStale: true,
    });
  });

  it("accepts only a positive integer runtime threshold", () => {
    expect(snapshotStaleAfterSeconds("3600")).toBe(3600);
    expect(snapshotStaleAfterSeconds("0")).toBe(DEFAULT_SNAPSHOT_STALE_AFTER_SECONDS);
    expect(snapshotStaleAfterSeconds("12.5")).toBe(DEFAULT_SNAPSHOT_STALE_AFTER_SECONDS);
  });
});
