/*
 * scripts/install_githooks.mjs: the pre-push gate replaces exactly the Git LFS
 * shim, only on Windows, and nothing else. Runs against a temporary hooks dir;
 * the shared one is never touched here.
 */
import { mkdtempSync, readFileSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { installGitHooks, lfsShim, WINDOWS_ONLY, SRC_DIR, lf } from "./install_githooks.mjs";

const failed = [];
const check = (label, ok, detail = "") => {
  console.log(`${ok ? "ok  " : "FAIL"} ${label}${!ok && detail ? `: ${detail}` : ""}`);
  if (!ok) failed.push(label);
};
const fresh = () => mkdtempSync(join(tmpdir(), "cardz-hooks-"));
const state = (results, name) => results.find((r) => r.name === name)?.state;

// The real 350-byte shim Git LFS writes (measured 2026-09-25 with od).
const SHIM = lfsShim("pre-push");
check("the LFS shim fixture is the measured 350 bytes", Buffer.byteLength(SHIM) === 350, String(Buffer.byteLength(SHIM)));
check("the tracked pre-push is Windows-only", WINDOWS_ONLY.has("pre-push") && WINDOWS_ONLY.has("cardz_prepush.py"));

{
  const dir = fresh();
  writeFileSync(join(dir, "pre-push"), SHIM);
  const results = installGitHooks({ platform: "linux", dir });
  check("linux: pre-push is skipped", state(results, "pre-push") === "skipped-platform");
  check("linux: cardz_prepush.py is skipped", state(results, "cardz_prepush.py") === "skipped-platform");
  check("linux: the LFS shim is left byte for byte", readFileSync(join(dir, "pre-push"), "utf8") === SHIM);
  check("linux: the orchestrator is not written", !existsSync(join(dir, "cardz_prepush.py")));
  check("linux: commit-msg is still installed", state(results, "commit-msg") === "installed");
  rmSync(dir, { recursive: true, force: true });
}

{
  const dir = fresh();
  writeFileSync(join(dir, "pre-push"), SHIM);
  const dry = installGitHooks({ platform: "win32", dir, dryRun: true });
  check("win32 dry run reports the replacement", state(dry, "pre-push") === "replaced-lfs-shim");
  check("win32 dry run writes nothing", readFileSync(join(dir, "pre-push"), "utf8") === SHIM);
  const results = installGitHooks({ platform: "win32", dir });
  check("win32: the exact LFS shim is replaced", state(results, "pre-push") === "replaced-lfs-shim");
  const installed = readFileSync(join(dir, "pre-push"), "utf8");
  check("win32: the installed hook is the tracked one", installed === lf(readFileSync(join(SRC_DIR, "pre-push"), "utf8")));
  check("win32: the installed hook still chains git lfs", installed.includes('git lfs pre-push "$@"'));
  check("win32: a second run is a no-op", state(installGitHooks({ platform: "win32", dir }), "pre-push") === "ok");
  rmSync(dir, { recursive: true, force: true });
}

{
  const dir = fresh();
  const offByOne = SHIM.replace("exit 2", "exit 3");
  writeFileSync(join(dir, "pre-push"), offByOne);
  const results = installGitHooks({ platform: "win32", dir });
  check("win32: a shim one byte off is foreign", state(results, "pre-push") === "foreign");
  check("win32: a foreign hook is never overwritten", readFileSync(join(dir, "pre-push"), "utf8") === offByOne);
  rmSync(dir, { recursive: true, force: true });
}

if (failed.length) {
  console.error(`\n${failed.length} FAILED: ${failed.join(" | ")}`);
  process.exit(1);
}
console.log("\ngit hook installer rules hold");
