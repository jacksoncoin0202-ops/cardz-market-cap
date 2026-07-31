import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execute = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
// Ubuntu 24.04 冇 /usr/bin/python，Windows 嘅 python3 又係 Store 假 alias，所以兩邊各用各嘅名。
const PYTHON = process.env.CARDZ_PYTHON ?? (process.platform === "win32" ? "python" : "python3");

test("a local finalization failure cannot invoke remote pointer publication", async () => {
  const program = String.raw`
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, 'pipelines')
from run_daily import finalize_local_candidate_then_publish
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    marker = root / 'remote-pointer-advanced'
    try:
        finalize_local_candidate_then_publish(
            root / 'candidate.json', root / 'missing-image-qc.json', root / 'assets',
            None,
            [sys.executable, '-c', f"from pathlib import Path; Path(r'{marker}').write_text('advanced')"],
            False, 10,
        )
    except Exception:
        pass
    print(json.dumps({'remotePointerAdvanced': marker.exists()}))
`;
  const result = JSON.parse((await execute(PYTHON, ["-c", program], { cwd: root })).stdout.trim());
  assert.equal(result.remotePointerAdvanced, false);
});

test("run_daily delegates sole promotion to the versioned publisher", async () => {
  const source = await readFile(path.join(root, "pipelines/run_daily.py"), "utf8");
  const helper = source.slice(source.indexOf("def finalize_local_candidate_then_publish"), source.indexOf("def main()"));
  assert.ok(helper.indexOf("verify_images.py") < helper.indexOf("run_checked(publish_command"));
  assert.equal(helper.includes("promote_file("), false);
  assert.equal(helper.includes("quarantine_unreferenced_assets"), false);
});

test("the exact evaluation becomes exportable only after the post-derive audit", async () => {
  const source = await readFile(path.join(root, "pipelines/run_daily.py"), "utf8");
  const main = source.slice(source.indexOf("def main()"));
  const audit = main.indexOf("post_derive_audit_command(");
  const passGate = main.indexOf("mark_market_evaluation_passed_command(evaluation_id)");
  const exportCandidate = main.indexOf("export_command =");
  assert.ok(audit >= 0);
  assert.ok(audit < passGate);
  assert.ok(passGate < exportCandidate);
});

test("canonical DB QC must pass before snapshot export or pointer publication", async () => {
  const source = await readFile(path.join(root, "pipelines/run_daily.py"), "utf8");
  const main = source.slice(source.indexOf("def main()"));
  const databaseQc = main.indexOf("canonical_db_qc_command(pipeline_run_id)");
  const exportCandidate = main.indexOf("export_command =");
  const publisher = main.indexOf('"pipelines/publish-snapshot.mjs"');
  assert.ok(databaseQc >= 0);
  assert.ok(databaseQc < exportCandidate);
  assert.ok(exportCandidate < publisher);
});
