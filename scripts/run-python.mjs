#!/usr/bin/env node
// npm script 冇 platform 分支能力，所以統一經呢個 runner 揀 interpreter：
// Ubuntu 24.04 淨係有 /usr/bin/python3（冇 python），Windows 嘅 python3 通常係 Microsoft Store 假 alias。
// CARDZ_PYTHON 同 scripts/backend.sh、deploy/systemd/run-cardz-daily.sh、pipelines/run_daily.ps1 用同一個名。
import { spawnSync } from "node:child_process";

const interpreter = process.env.CARDZ_PYTHON ?? (process.platform === "win32" ? "python" : "python3");
const result = spawnSync(interpreter, process.argv.slice(2), { stdio: "inherit" });

if (result.error) {
  console.error(`cannot start Python interpreter "${interpreter}": ${result.error.message}`);
  process.exit(127);
}
process.exit(result.status ?? 1);
