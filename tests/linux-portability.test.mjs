import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const selfPath = fileURLToPath(import.meta.url);
const root = path.resolve(path.dirname(selfPath), "..");

// Ubuntu 24.04 只有 /usr/bin/python3，冇 /usr/bin/python，亦冇 python-is-python3。
// 任何 spawn 字面 "python" 嘅位一上裸 server 即 ENOENT。CI 用 actions/setup-python，
// 佢會整個 python shim，所以呢個 bug 喺 CI 永遠見唔到——只有呢條測試守得住。
// 改法係經 CARDZ_PYTHON ?? (win32 ? "python" : "python3")，唔可以硬寫任何一邊：
// Windows 嘅 python3 通常係 Microsoft Store 假 alias，會彈商店而唔係跑 Python。
const SPAWNS_LITERAL_PYTHON = /(?:exec|execSync|execFile|execFileSync|spawn|spawnSync|execute)\s*\(\s*["'`]python3?\b/;
const SCRIPT_INVOKES_PYTHON = /(?:^|&&|\|\||;|\|)\s*python3?\b/;

const SCANNED_DIRECTORIES = ["tests", "scripts", "pipelines"];
const SKIPPED_DIRECTORY_NAMES = new Set(["node_modules", "__pycache__", "dist", ".next"]);

async function collectModules(directory) {
  const collected = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    if (SKIPPED_DIRECTORY_NAMES.has(entry.name)) continue;
    const full = path.join(directory, entry.name);
    if (entry.isDirectory()) {
      collected.push(...(await collectModules(full)));
    } else if (entry.name.endsWith(".mjs") && full !== selfPath) {
      collected.push(full);
    }
  }
  return collected;
}

test("no JavaScript module spawns a literal python interpreter name", async () => {
  const modules = (await Promise.all(SCANNED_DIRECTORIES.map((name) => collectModules(path.join(root, name))))).flat();
  assert.ok(modules.length >= 5, "portability scan found no modules to check");

  const offenders = [];
  for (const file of modules) {
    const lines = (await readFile(file, "utf8")).split(/\r?\n/);
    lines.forEach((line, index) => {
      if (SPAWNS_LITERAL_PYTHON.test(line)) {
        offenders.push(`${path.relative(root, file)}:${index + 1}: ${line.trim()}`);
      }
    });
  }

  assert.deepEqual(
    offenders,
    [],
    `spawn 咗字面 python，Ubuntu 24.04 會 ENOENT。改用 CARDZ_PYTHON ?? (process.platform === "win32" ? "python" : "python3")：\n${offenders.join("\n")}`,
  );
});

test("no npm script invokes a literal python interpreter name", async () => {
  const manifest = JSON.parse(await readFile(path.join(root, "package.json"), "utf8"));
  const scripts = Object.entries(manifest.scripts ?? {});
  assert.ok(scripts.length > 0, "portability scan found no npm scripts to check");

  const offenders = scripts
    .filter(([, command]) => SCRIPT_INVOKES_PYTHON.test(command))
    .map(([name, command]) => `${name}: ${command}`);

  assert.deepEqual(
    offenders,
    [],
    `npm script 直接叫 python，Ubuntu 24.04 會 ENOENT。改行 node scripts/run-python.mjs：\n${offenders.join("\n")}`,
  );
});

test("the portability detectors actually reject the regressions they guard against", () => {
  for (const regression of [
    'const { stdout } = await execute("python", ["pipelines/g10_ingest.py"], { cwd: root });',
    "spawnSync('python', args);",
    'execFileSync("python3", ["-c", program]);',
    'exec("python pipelines/run_daily.py")',
  ]) {
    assert.ok(SPAWNS_LITERAL_PYTHON.test(regression), `detector missed: ${regression}`);
  }

  for (const allowed of [
    'const python = (await execute("where.exe", ["python"])).stdout.trim();',
    'const PYTHON = process.env.CARDZ_PYTHON ?? (process.platform === "win32" ? "python" : "python3");',
    "spawnSync(interpreter, process.argv.slice(2), { stdio: \"inherit\" });",
  ]) {
    assert.equal(SPAWNS_LITERAL_PYTHON.test(allowed), false, `detector false-positived on: ${allowed}`);
  }

  for (const regression of [
    "python -m unittest discover -s tests",
    "npm run build && python pipelines/verify_images.py",
    "python3 pipelines/verify_images.py --strict-semantic",
  ]) {
    assert.ok(SCRIPT_INVOKES_PYTHON.test(regression), `detector missed: ${regression}`);
  }

  for (const allowed of [
    "node scripts/run-python.mjs pipelines/verify_images.py",
    "npm run test --workspaces --if-present && node --test tests/*.test.mjs && npm run test:python",
  ]) {
    assert.equal(SCRIPT_INVOKES_PYTHON.test(allowed), false, `detector false-positived on: ${allowed}`);
  }
});
