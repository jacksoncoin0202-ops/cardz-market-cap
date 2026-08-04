import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

function parseArgs(argv) {
  const options = {
    pointer: null,
    url: "http://127.0.0.1:3000/api/health",
    attempts: 30,
    delayMs: 1000,
  };
  for (let index = 0; index < argv.length; index += 1) {
    const value = argv[index];
    if (value === "--pointer") options.pointer = argv[++index];
    else if (value === "--url") options.url = argv[++index];
    else if (value === "--attempts") options.attempts = Number(argv[++index]);
    else if (value === "--delay-ms") options.delayMs = Number(argv[++index]);
    else throw new Error(`Unknown argument: ${value}`);
  }
  if (!options.pointer) throw new Error("--pointer is required");
  if (!Number.isInteger(options.attempts) || options.attempts < 1 || options.attempts > 120) {
    throw new Error("--attempts must be an integer from 1 to 120");
  }
  if (!Number.isInteger(options.delayMs) || options.delayMs < 0 || options.delayMs > 30_000) {
    throw new Error("--delay-ms must be an integer from 0 to 30000");
  }
  return options;
}

export function verifyHealthPayload(payload, expectedGeneration) {
  if (!payload || typeof payload !== "object") throw new Error("health response is not an object");
  if (payload.generation !== expectedGeneration) {
    throw new Error(`health generation mismatch: expected ${expectedGeneration}, received ${payload.generation ?? "missing"}`);
  }
  if (payload.snapshotStale !== false) throw new Error("health snapshot is stale");
  if (!Number.isInteger(payload.cards) || payload.cards < 1) throw new Error("health card count is empty");
  if (!Number.isInteger(payload.snapshotAgeSeconds) || payload.snapshotAgeSeconds < 0) {
    throw new Error("health snapshot age is invalid");
  }
  return {
    generation: payload.generation,
    cards: payload.cards,
    snapshotAgeSeconds: payload.snapshotAgeSeconds,
  };
}

async function expectedGeneration(pointerPath) {
  const pointer = JSON.parse(await readFile(pointerPath, "utf8"));
  if (
    pointer?.schemaVersion !== 1
    || typeof pointer.generationId !== "string"
    || !/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/.test(pointer.generationId)
  ) {
    throw new Error("runtime snapshot pointer is invalid");
  }
  return pointer.generationId;
}

async function sleep(milliseconds) {
  await new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export async function verifyRuntimeHealth(options, fetcher = fetch) {
  const generation = await expectedGeneration(options.pointer);
  let lastError = new Error("health endpoint was not checked");
  for (let attempt = 1; attempt <= options.attempts; attempt += 1) {
    try {
      const response = await fetcher(options.url, { cache: "no-store" });
      if (!response.ok) throw new Error(`health endpoint returned HTTP ${response.status}`);
      const result = verifyHealthPayload(await response.json(), generation);
      return { ...result, attempts: attempt };
    } catch (error) {
      lastError = error instanceof Error ? error : new Error("health verification failed");
      if (attempt < options.attempts) await sleep(options.delayMs);
    }
  }
  throw lastError;
}

async function main() {
  const result = await verifyRuntimeHealth(parseArgs(process.argv.slice(2)));
  process.stdout.write(`${JSON.stringify({ status: "ok", ...result })}\n`);
}

if (process.argv[1] && pathToFileURL(process.argv[1]).href === import.meta.url) {
  main().catch((error) => {
    process.stderr.write(`${error instanceof Error ? error.message : "runtime health verification failed"}\n`);
    process.exitCode = 1;
  });
}
