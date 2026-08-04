/**
 * Operator MySQL loader for CARDZ dual-mode frontend.
 * Used only when CARDZ_DATA_MODE=operator.
 */
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";
import type { MarketViewSnapshot } from "./types";
import { normaliseSnapshot } from "./snapshot";
import type { PublicMarketSnapshot } from "@cardz/market-data";

function repoRoot(): string {
  const fromEnv = (process.env.CARDZ_REPO_ROOT || "").trim();
  if (fromEnv && existsSync(join(fromEnv, "pipelines", "operator_control.py"))) {
    return resolve(fromEnv);
  }
  const candidates = [
    process.cwd(),
    resolve(process.cwd(), ".."),
    resolve(process.cwd(), "..", ".."),
    resolve(process.cwd(), "..", "..", ".."),
  ];
  for (const c of candidates) {
    if (existsSync(join(c, "pipelines", "operator_control.py"))) return c;
  }
  const known = "C:/Users/jackson0202/Documents/Playground/cardz-market-cap";
  if (existsSync(join(known, "pipelines", "operator_control.py"))) return known;
  return process.cwd();
}

export function isOperatorMode(): boolean {
  const mode = (process.env.CARDZ_DATA_MODE || "").trim().toLowerCase();
  return mode === "operator";
}

export async function loadOperatorSnapshotFromDb(): Promise<MarketViewSnapshot> {
  if (!isOperatorMode()) {
    throw new Error("operator DB loader blocked outside CARDZ_DATA_MODE=operator");
  }
  const root = repoRoot();
  // Local engineering mode: always re-read live MySQL via operator_control.
  // The .live.json file is only a transient IPC payload for FE, not a product snapshot.
  // Never silently fall back to a stale operator-snapshot.json.
  const scriptRel = "pipelines/operator_control.py";
  const outRel = "data/runtime/operator/operator-snapshot.live.json";
  const scriptAbs = join(root, "pipelines", "operator_control.py");
  const outPath = join(root, "data", "runtime", "operator", "operator-snapshot.live.json");
  if (!existsSync(scriptAbs)) {
    throw new Error(`operator_control.py missing: ${scriptAbs}`);
  }
  const python = process.env.CARDZ_PYTHON?.trim() || "python";
  // Windows .cmd wrappers require a shell; bare spawnSync(..., shell:false) throws EINVAL.
  const needsShell = process.platform === "win32" && /\.(cmd|bat)$/i.test(python);
  const result = spawnSync(
    python,
    ["-X", "utf8", scriptRel, "export-operator-snapshot", "--output", outRel],
    {
      cwd: root,
      encoding: "utf8",
      env: process.env,
      maxBuffer: 64 * 1024 * 1024,
      shell: needsShell,
      windowsHide: true,
    },
  );
  if (result.error) {
    throw new Error(`operator live DB export spawn failed: ${result.error.message}`);
  }
  if (result.status !== 0) {
    const detail = `${result.stderr || ""}\n${result.stdout || ""}`.trim() || "operator live DB export failed";
    throw new Error(`operator live DB export failed: ${detail}`);
  }
  if (!existsSync(outPath)) {
    throw new Error(`operator live DB export missing output: ${outPath}`);
  }
  const fs = await import("node:fs/promises");
  const raw = await fs.readFile(outPath, "utf8");
  const canonical = JSON.parse(raw) as PublicMarketSnapshot;
  return normaliseSnapshot(canonical as never);
}
