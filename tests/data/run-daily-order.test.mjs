import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { promisify } from "node:util";
import { fileURLToPath } from "node:url";

const execute = promisify(execFile);
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");

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
            root / 'seed.json', root / 'image-qc.json', root / 'quarantine',
            [sys.executable, '-c', f"from pathlib import Path; Path(r'{marker}').write_text('advanced')"],
            False, 10,
        )
    except Exception:
        pass
    print(json.dumps({'remotePointerAdvanced': marker.exists()}))
`;
  const result = JSON.parse((await execute("python", ["-c", program], { cwd: root })).stdout.trim());
  assert.equal(result.remotePointerAdvanced, false);
});

test("run_daily orders local verify, promote, and quarantine before publish", async () => {
  const source = await readFile(path.join(root, "pipelines/run_daily.py"), "utf8");
  const helper = source.slice(source.indexOf("def finalize_local_candidate_then_publish"), source.indexOf("def main()"));
  assert.ok(helper.indexOf("verify_images.py") < helper.indexOf("promote_file(candidate_image_manifest"));
  assert.ok(helper.indexOf("promote_file(candidate_snapshot") < helper.indexOf("quarantine_unreferenced_assets"));
  assert.ok(helper.indexOf("quarantine_unreferenced_assets") < helper.indexOf("run_checked(publish_command"));
});
