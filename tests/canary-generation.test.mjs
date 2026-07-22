import assert from "node:assert/strict";
import test from "node:test";

import { resolveRenderedGeneration } from "../scripts/canary-public.mjs";

test("canary uses the generation response header when present", () => {
  const response = new Response("", { headers: { "X-CARDZ-Generation": "daily_header" } });
  assert.equal(resolveRenderedGeneration(response, '<main data-cardz-generation="daily_body"></main>'), "daily_header");
});

test("canary falls back to the exact rendered generation attribute", () => {
  const response = new Response("");
  assert.equal(resolveRenderedGeneration(response, '<div class="page-shell" data-cardz-generation="daily_candidate"></div>'), "daily_candidate");
  assert.equal(resolveRenderedGeneration(response, '<div data-other-generation="daily_wrong"></div>'), "");
});

test("canary rejects unsafe header generation values", () => {
  const response = new Response("", { headers: { "X-CARDZ-Generation": "../private" } });
  assert.throws(() => resolveRenderedGeneration(response, ""), /unsafe CARDZ generation ID/);
});
