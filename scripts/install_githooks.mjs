/*
 * scripts/install_githooks.mjs — 將 scripts/githooks/ 入面嘅 hook 裝落 git 共用 hooks 目錄。
 *
 * 點解唔用 core.hooksPath：呢個 checkout 係 linked worktree（`.git` 係個指標檔），
 * core.hooksPath 會寫落**共用** config，即係連隔籬個 checkout 都改埋；而佢一設低，
 * 原本 `.git/hooks` 入面四個 Git LFS shim（post-checkout / post-commit / post-merge /
 * pre-push）就會即刻唔再行 —— 為咗裝一個 commit-msg 而整爛 LFS，唔抵。
 * 直接寫入共用 hooks 目錄就冇呢個問題：commit-msg 個位本身係空嘅，LFS 嗰四個唔郁。
 *
 * 安全掣：目標檔如果已經存在而且**唔係我哋嗰個**（冇 `cardz-githook:` marker），
 * 一律唔覆蓋，直接報錯 —— 唔准靜靜蓋走人哋個 hook。
 *
 * 用法：
 *   node scripts/install_githooks.mjs          # 裝／更新
 *   node scripts/install_githooks.mjs --check  # 淨係報狀態，唔寫嘢（exit 1 = 未裝／有偏差）
 *
 * scripts/test-deploy-tag-contract.mjs 會 import 呢度嘅 installGitHooks()。
 */

import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync, readdirSync, existsSync, mkdirSync, chmodSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
export const REPO_ROOT = resolve(HERE, "..");
export const SRC_DIR = join(HERE, "githooks");
export const MARKER = "cardz-githook:";

/* hook 一定要 LF。checkout 出嚟可能係 CRLF（Windows autocrlf），一個 CRLF 喺 shebang
   嗰行就會變 `bad interpreter: /bin/sh^M`，個 hook 靜靜唔行 = 等於冇裝。 */
export const lf = (s) => s.replace(/\r\n/g, "\n");

export function hooksDir() {
  const out = execFileSync("git", ["rev-parse", "--git-common-dir"], {
    cwd: REPO_ROOT,
    encoding: "utf8",
  }).trim();
  return join(resolve(REPO_ROOT, out), "hooks");
}

export function trackedHooks() {
  return readdirSync(SRC_DIR)
    .filter((n) => !n.startsWith("."))
    .map((name) => ({ name, src: join(SRC_DIR, name), body: lf(readFileSync(join(SRC_DIR, name), "utf8")) }));
}

/** @returns {{name:string, state:"ok"|"installed"|"updated"|"foreign", path:string}[]} */
export function installGitHooks({ dryRun = false } = {}) {
  const dir = hooksDir();
  if (!existsSync(dir)) {
    if (dryRun) return trackedHooks().map((h) => ({ name: h.name, state: "installed", path: join(dir, h.name) }));
    mkdirSync(dir, { recursive: true });
  }
  return trackedHooks().map((h) => {
    const dest = join(dir, h.name);
    if (existsSync(dest)) {
      const cur = lf(readFileSync(dest, "utf8"));
      if (cur === h.body) return { name: h.name, state: "ok", path: dest };
      if (!cur.includes(MARKER)) return { name: h.name, state: "foreign", path: dest };
      if (!dryRun) writeFileSync(dest, h.body, { encoding: "utf8" });
      return { name: h.name, state: "updated", path: dest };
    }
    if (!dryRun) writeFileSync(dest, h.body, { encoding: "utf8" });
    return { name: h.name, state: "installed", path: dest };
  }).map((r) => {
    if (!dryRun && r.state !== "foreign") { try { chmodSync(r.path, 0o755); } catch { /* NTFS 冇所謂 */ } }
    return r;
  });
}

const isMain = process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url));
if (isMain) {
  const dryRun = process.argv.includes("--check");
  let results;
  try {
    results = installGitHooks({ dryRun });
  } catch (err) {
    console.error(`install_githooks: 裝唔到 —— ${err.message}`);
    process.exit(1);
  }
  for (const r of results) {
    const say = { ok: "已經係最新", installed: "裝咗", updated: "更新咗", foreign: "⚠️ 已經有另一個 hook 喺度，冇覆蓋" }[r.state];
    console.log(`${r.name}: ${say}  → ${r.path}`);
  }
  const bad = results.filter((r) => r.state === "foreign" || (dryRun && r.state !== "ok"));
  if (bad.length) {
    if (dryRun) console.error("未裝／有偏差，行 `node scripts/install_githooks.mjs` 裝返。");
    process.exit(1);
  }
}
