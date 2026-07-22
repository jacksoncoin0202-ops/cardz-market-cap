#!/usr/bin/env node

import { execFileSync } from "node:child_process";
import { mkdir, rename, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const generationId = process.env.CARDZ_CANDIDATE_GENERATION_ID ?? "";
const receiptPath = process.env.CARDZ_CANARY_RECEIPT_PATH ?? "";
const origin = process.env.CARDZ_CANARY_ORIGIN ?? "";

if (receiptPath) {
  await unlink(receiptPath).catch((error) => {
    if (error.code !== "ENOENT") throw error;
  });
}
if (!generationId || !receiptPath || !origin) {
  throw new Error("generation canary requires CARDZ_CANDIDATE_GENERATION_ID, CARDZ_CANARY_RECEIPT_PATH, and CARDZ_CANARY_ORIGIN");
}

let canaryCommand = [
  process.execPath,
  "scripts/canary-public.mjs",
  "--origin",
  origin,
  "--expect-generation",
  generationId,
  "--discovery",
  "private",
];
if (process.env.CARDZ_HOOK_TEST_MODE === "1" && process.env.NODE_ENV === "test") {
  const raw = process.env.CARDZ_PUBLIC_CANARY_COMMAND_JSON ?? "";
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed) || parsed.length === 0 || parsed.some((part) => typeof part !== "string" || !part)) {
    throw new Error("CARDZ_PUBLIC_CANARY_COMMAND_JSON must be a non-empty JSON string array in hook test mode");
  }
  canaryCommand = [...parsed, "--origin", origin, "--expect-generation", generationId];
}

execFileSync(
  canaryCommand[0],
  canaryCommand.slice(1),
  { cwd: root, env: process.env, stdio: "inherit" },
);

await mkdir(path.dirname(receiptPath), { recursive: true });
const temporary = `${receiptPath}.${process.pid}.tmp`;
await writeFile(temporary, `${JSON.stringify({ schemaVersion: 1, passed: true, generationId }, null, 2)}\n`);
await rename(temporary, receiptPath);
