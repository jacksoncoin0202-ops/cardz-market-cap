#!/usr/bin/env node
// Bake data/public/seed-snapshot.json out of the live 3308 DB.
//
// AWS production never touches MySQL: the container reads this one file plus
// the market-assets it names. Until now the bake was done by hand, which is
// why the file on main carried a generation nobody could re-derive. This
// script is the whole step -- same code path the FE uses in live-db mode, so
// a baked snapshot and a live-db FE cannot disagree about what a card is.
//
// It reuses apps/web/src/lib/live-db-snapshot.ts rather than re-querying,
// because a second implementation of the ranking projection is a second thing
// to keep in sync, and the two would drift on exactly the fields nobody looks
// at until a release. The copy in the build directory is patched to hand back
// the canonical PublicMarketSnapshot instead of the view model: the view model
// is what the FE renders, the canonical document is what gets written to disk
// and re-normalised on read.
//
// Baking also prunes: every market-asset the fresh snapshot does not name is
// moved out of data/public (into gitignored data/runtime, with a manifest, not
// deleted) before the release commit is built. That step used to live behind
// g10_public_snapshot.py --clean-assets, which the 036 chain never ran, so the
// public asset tree only ever grew -- 4,535 sha / 1.82 GB against 1,286 sha /
// 0.52 GB actually referenced. A prune nobody remembers to ask for is not a
// prune, so it is on by default here; --no-prune opts out.
//
//   node scripts/bake-public-snapshot.mjs [--output <path>] [--keep-build] [--no-prune]

import { createHash } from "node:crypto";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import {
  cpSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { assertPublicSurface } from "./public-surface-gate.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const WEB = join(ROOT, "apps", "web");
const LIB = join(WEB, "src", "lib");
const BUILD = join(WEB, ".next", "cache", "cardz-bake");
const SOURCES = ["live-db-snapshot.ts", "snapshot.ts", "types.ts"];

function arg(name, fallback = null) {
  const at = process.argv.indexOf(name);
  return at === -1 || at === process.argv.length - 1 ? fallback : process.argv[at + 1];
}

const OUTPUT = resolve(arg("--output", join(ROOT, "data", "public", "seed-snapshot.json")));

function patch(text, from, to, what) {
  if (!text.includes(from)) {
    throw new Error(
      `bake patch failed: ${what} -- expected to find ${JSON.stringify(from)} in the`
      + " live-db snapshot source. The source moved; fix this script rather than"
      + " baking a document it no longer describes.",
    );
  }
  return text.split(from).join(to);
}

rmSync(BUILD, { recursive: true, force: true });
mkdirSync(BUILD, { recursive: true });

for (const name of SOURCES) cpSync(join(LIB, name), join(BUILD, name));

let entry = readFileSync(join(BUILD, "live-db-snapshot.ts"), "utf8");
// server-only exists to make a build fail when this module reaches the client
// bundle. There is no bundle here.
entry = patch(entry, 'import "server-only";\n', "", "server-only guard");
entry = patch(
  entry,
  "    return normaliseSnapshot(snapshot);",
  "    return snapshot as unknown as MarketViewSnapshot;",
  "canonical snapshot handoff",
);
writeFileSync(join(BUILD, "live-db-snapshot.ts"), entry);

writeFileSync(join(BUILD, "tsconfig.json"), `${JSON.stringify({
  compilerOptions: {
    target: "ES2022",
    module: "commonjs",
    moduleResolution: "node",
    esModuleInterop: true,
    skipLibCheck: true,
    strict: false,
    noEmitOnError: false,
    outDir: "out",
  },
  include: SOURCES,
}, null, 2)}\n`);

const tsc = join(ROOT, "node_modules", "typescript", "bin", "tsc");
const compile = spawnSync(process.execPath, [tsc, "-p", join(BUILD, "tsconfig.json")], {
  cwd: BUILD,
  stdio: ["ignore", "pipe", "pipe"],
  encoding: "utf8",
});
// Type errors are expected and harmless: the canonical handoff above is a
// deliberate lie to the checker. Missing emit is not.
const compiled = join(BUILD, "out", "live-db-snapshot.js");
try {
  statSync(compiled);
} catch {
  process.stderr.write(`${compile.stdout ?? ""}${compile.stderr ?? ""}`);
  throw new Error("bake failed: tsc produced no live-db-snapshot.js");
}

process.env.CARDZ_REPO_ROOT ??= ROOT;

const { loadLiveDbSnapshot } = createRequire(join(BUILD, "bake.cjs"))(compiled);
const snapshot = await loadLiveDbSnapshot();

// 出街閘。呢度係 producer:兩種 serving mode(baked file / live-db)都經同一條
// code path,所以檢查放喺呢度先至蓋得晒兩邊。規則同兩條 ratchet 嘅解釋喺
// scripts/public-surface-gate.mjs;分開係為咗 scripts/test-public-surface-gate.mjs
// 唔使開 DB 都證得到佢真係會炸。
const gate = assertPublicSurface(snapshot);

const cards = snapshot.top100.length + snapshot.watchlist.length;
const body = Buffer.from(`${JSON.stringify(snapshot, null, 2)}\n`, "utf8");
mkdirSync(dirname(OUTPUT), { recursive: true });
const staging = `${OUTPUT}.bake-tmp`;
writeFileSync(staging, body);
renameSync(staging, OUTPUT);

if (!process.argv.includes("--keep-build")) rmSync(BUILD, { recursive: true, force: true });

// The asset names are what the release commit has to carry: a snapshot whose
// images are not committed alongside it publishes a wall of broken cards.
const assets = new Set();
for (const card of [...snapshot.top100, ...snapshot.watchlist]) {
  for (const reference of [card.image?.src, ...Object.values(card.image?.variants ?? {})]) {
    if (typeof reference === "string" && reference) assets.add(reference.split("/").pop());
  }
}

let prune = { skipped: true };
if (!process.argv.includes("--no-prune")) {
  // CARDZ_PYTHON is the same name scripts/run-python.mjs, scripts/backend.sh and
  // pipelines/run_daily.ps1 already use.
  const python = process.env.CARDZ_PYTHON ?? (process.platform === "win32" ? "python" : "python3");
  const moved = spawnSync(python, ["-X", "utf8", join(ROOT, "scripts", "prune_public_assets.py"), "--snapshot", OUTPUT], {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
    encoding: "utf8",
  });
  if (moved.status !== 0) {
    process.stderr.write(`${moved.stdout ?? ""}${moved.stderr ?? ""}`);
    throw new Error("bake failed: asset prune did not run -- the release tree would carry unreferenced images");
  }
  prune = JSON.parse(moved.stdout);
}

process.stdout.write(`${JSON.stringify({
  output: OUTPUT,
  generation: snapshot.generation,
  cards,
  top100: snapshot.top100.length,
  watchlist: snapshot.watchlist.length,
  bytes: body.length,
  sha256: createHash("sha256").update(body).digest("hex"),
  referencedAssets: assets.size,
  gate,
  prune,
}, null, 2)}\n`);
