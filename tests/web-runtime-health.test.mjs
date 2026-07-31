import assert from "node:assert/strict";
import { test } from "node:test";
import { verifyHealthPayload } from "../apps/web/scripts/verify-runtime-health.mjs";

test("runtime health accepts only the promoted fresh generation", () => {
  assert.deepEqual(
    verifyHealthPayload(
      {
        generation: "canonical_20260728",
        snapshotStale: false,
        snapshotAgeSeconds: 90,
        cards: 428,
      },
      "canonical_20260728",
    ),
    {
      generation: "canonical_20260728",
      snapshotAgeSeconds: 90,
      cards: 428,
    },
  );
});

test("runtime health rejects stale or mismatched generations", () => {
  assert.throws(
    () => verifyHealthPayload(
      {
        generation: "canonical_old",
        snapshotStale: false,
        snapshotAgeSeconds: 90,
        cards: 428,
      },
      "canonical_new",
    ),
    /generation mismatch/,
  );
  assert.throws(
    () => verifyHealthPayload(
      {
        generation: "canonical_new",
        snapshotStale: true,
        snapshotAgeSeconds: 172801,
        cards: 428,
      },
      "canonical_new",
    ),
    /snapshot is stale/,
  );
});
