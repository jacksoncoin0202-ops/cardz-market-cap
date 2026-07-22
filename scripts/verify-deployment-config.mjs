#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const failures = [];

function fail(message) {
  failures.push(message);
}

function read(relativePath) {
  return fs.readFileSync(path.join(root, relativePath), "utf8");
}

function bucketFor(environment, name) {
  const buckets = environment?.r2_buckets ?? [];
  const binding = buckets.find((entry) => entry.binding === "MARKET_DATA");
  if (!binding) {
    fail(`${name} must declare the private MARKET_DATA R2 binding.`);
    return null;
  }
  if (binding.preview_bucket_name !== binding.bucket_name) {
    fail(`${name} preview bucket must not cross an environment boundary.`);
  }
  return binding.bucket_name;
}

const wrangler = JSON.parse(read("apps/web/wrangler.jsonc"));
const environments = {
  local: wrangler,
  canary: wrangler.env?.canary,
  staging: wrangler.env?.staging,
  production: wrangler.env?.production,
};

for (const [name, environment] of Object.entries(environments)) {
  if (!environment) {
    fail(`wrangler environment ${name} is missing.`);
    continue;
  }
  if (environment.upload_source_maps !== false) {
    fail(`${name} must set upload_source_maps to false.`);
  }
  if (environment.vars?.CARDZ_ENVIRONMENT !== name) {
    fail(`${name} CARDZ_ENVIRONMENT is inconsistent.`);
  }
  const expectedPointer = name === "canary" ? "candidate.json" : "latest.json";
  if (environment.vars?.MARKET_DATA_POINTER_KEY !== expectedPointer) {
    fail(`${name} must use the validated ${expectedPointer} pointer contract.`);
  }
  if (name === "production" && environment.vars?.MARKET_DATA_ALLOW_DEMO !== "false") {
    fail("production must reject demo generations.");
  }
  if (name !== "production" && environment.vars?.MARKET_DATA_ALLOW_DEMO !== "true") {
    fail(`${name} must declare demo behavior explicitly.`);
  }
  if (environment.assets?.binding !== "ASSETS") {
    fail(`${name} must declare its non-inherited ASSETS binding.`);
  }
}

const names = Object.values(environments).map((environment) => environment?.name);
if (new Set(names).size !== names.length) fail("Worker names must be distinct across environments.");

const bucketEntries = Object.fromEntries(
  Object.entries(environments).map(([name, environment]) => [name, bucketFor(environment, name)]),
);
if (Object.values(bucketEntries).some((bucket) => !bucket)) {
  fail("Every environment must declare a MARKET_DATA bucket.");
} else {
  if (bucketEntries.canary !== bucketEntries.staging) fail("Canary and staging must share the staging R2 bucket.");
  if (new Set([bucketEntries.local, bucketEntries.staging, bucketEntries.production]).size !== 3) {
    fail("MARKET_DATA buckets must be distinct across local, staging, and production.");
  }
}
if (environments.production?.workers_dev !== false) {
  fail("production must not be exposed through workers.dev.");
}

const serializedConfig = JSON.stringify(wrangler);
if (/https?:\/\//i.test(serializedConfig)) fail("Wrangler config must not contain an upstream URL.");
if (/\b(?:g10|gemrate|snkrdunk|sneakerdunk|ebay)\b/i.test(serializedConfig)) {
  fail("Wrangler config must not disclose a private data provider.");
}

const nextConfig = read("apps/web/next.config.ts");
for (const required of [
  "productionBrowserSourceMaps: false",
  "Content-Security-Policy",
  "X-Content-Type-Options",
  "X-Frame-Options",
  "Strict-Transport-Security",
  "X-CARDZ-Build",
]) {
  if (!nextConfig.includes(required)) fail(`Next config is missing ${required}.`);
}
if (/connect-src[^\n]*https?:/i.test(nextConfig)) {
  fail("CSP connect-src must not disclose or permit an upstream HTTP origin.");
}

const envExample = read(".env.example");
for (const secretName of ["GEMRATE_API_KEY", "CARDZ_JLP_MYSQL_DSN"]) {
  const match = envExample.match(new RegExp(`^${secretName}=(.*)$`, "m"));
  if (!match || match[1].trim() !== "") fail(`${secretName} must remain blank in .env.example.`);
}

const gitignore = read(".gitignore");
for (const required of [".dev.vars", "data/private/g10/", "data/private/logs/", "data/private/publish-staging/"]) {
  if (!gitignore.includes(required)) fail(`.gitignore is missing ${required}.`);
}

if (failures.length > 0) {
  for (const failure of failures) console.error(`FAIL ${failure}`);
  console.error(`Deployment config verification failed with ${failures.length} error(s).`);
  process.exitCode = 1;
} else {
  console.log("PASS deployment config: isolated canary/staging/production Workers, private R2 bindings, no source-map upload, hardened headers.");
  console.log("NOTE production has no route in source control; adding the approved route remains a cutover gate.");
}
