import { execFileSync } from "node:child_process";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, renameSync, rmSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const repoRoot = path.resolve(appRoot, "..", "..");
const cli = path.join(repoRoot, "node_modules", "@opennextjs", "cloudflare", "dist", "cli", "index.js");
const publicRoot = path.join(appRoot, "public");
const temporaryRoot = mkdtempSync(path.join(os.tmpdir(), "cardz-cloudflare-public-"));
const publicBackup = path.join(temporaryRoot, "public");

try {
  if (existsSync(publicRoot)) renameSync(publicRoot, publicBackup);
  mkdirSync(publicRoot, { recursive: true });
  const placeholder = path.join(publicBackup, "card-placeholder.svg");
  if (existsSync(placeholder)) copyFileSync(placeholder, path.join(publicRoot, "card-placeholder.svg"));

  execFileSync(process.execPath, [cli, "build"], {
    cwd: appRoot,
    env: { ...process.env, CARDZ_CLOUDFLARE_BUILD: "1" },
    stdio: "inherit",
  });
} finally {
  rmSync(publicRoot, { recursive: true, force: true });
  if (existsSync(publicBackup)) renameSync(publicBackup, publicRoot);
  rmSync(temporaryRoot, { recursive: true, force: true });
}
