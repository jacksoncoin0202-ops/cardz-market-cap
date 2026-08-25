#!/usr/bin/env python3
"""Exercise the real V2 resume function against a divergent local main."""
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "scripts" / "daily_public_release.sh").read_text(encoding="utf-8")
START = SOURCE.index("v2_prepare_resume_commit() {")
END = SOURCE.index('\ntest -e "$RELEASE_REPO/.git"', START)
FUNCTION = SOURCE[START:END]

script = f"""#!/usr/bin/env bash
set -euo pipefail
root=$(mktemp -d /tmp/cardz-v2-resume-test.XXXXXX)
trap 'rm -rf -- "$root"' EXIT
bare="$root/origin.git"
repo="$root/release"
git init --bare --initial-branch=main "$bare" >/dev/null
git clone "$bare" "$repo" >/dev/null 2>&1
git -C "$repo" config user.email test@example.invalid
git -C "$repo" config user.name cardz-test
mkdir -p "$repo/data/public"
printf '{{"generation":{{"id":"base"}}}}\n' > "$repo/data/public/seed-snapshot.json"
printf 'base\n' > "$repo/data/public/box-subset.json"
git -C "$repo" add -- data/public/seed-snapshot.json data/public/box-subset.json
git -C "$repo" commit -m base >/dev/null
git -C "$repo" push origin HEAD:main >/dev/null 2>&1
git -C "$repo" switch -c saved >/dev/null
printf '{{"generation":{{"id":"saved"}}}}\n' > "$repo/data/public/seed-snapshot.json"
printf 'saved\n' > "$repo/data/public/box-subset.json"
git -C "$repo" add -- data/public/seed-snapshot.json data/public/box-subset.json
git -C "$repo" commit -m saved-release >/dev/null
saved=$(git -C "$repo" rev-parse HEAD)
snapshot_sha=$(git -C "$repo" show "$saved:data/public/seed-snapshot.json" | sha256sum | awk '{{print $1}}')
tree_sha=$(git -C "$repo" ls-tree -r "$saved" -- data/public | sha256sum | awk '{{print $1}}')
git -C "$repo" switch main >/dev/null
printf 'human main advance\n' > "$repo/README.md"
git -C "$repo" add -- README.md
git -C "$repo" commit -m human-main-advance >/dev/null
git -C "$repo" push origin HEAD:main >/dev/null 2>&1
remote_head=$(git -C "$repo" rev-parse HEAD)
RELEASE_REPO="$repo"
generation=saved
V2_RESUME_PARENT=""
V2_RESUME_WORKTREE=""
V2_RESUME_COMMIT=""
{FUNCTION}
v2_prepare_resume_commit "$saved" "$snapshot_sha" "$tree_sha"
test -n "$V2_RESUME_COMMIT"
test "$V2_RESUME_COMMIT" != "$saved"
test "$(git -C "$repo" rev-parse "$V2_RESUME_COMMIT^")" = "$remote_head"
test "$(git -C "$repo" show "$V2_RESUME_COMMIT:README.md")" = 'human main advance'
test "$(git -C "$repo" show "$V2_RESUME_COMMIT:data/public/box-subset.json")" = saved
test "$(git -C "$repo" show "$V2_RESUME_COMMIT:data/public/seed-snapshot.json" | sha256sum | awk '{{print $1}}')" = "$snapshot_sha"
test "$(git -C "$repo" ls-tree -r "$V2_RESUME_COMMIT" -- data/public | sha256sum | awk '{{print $1}}')" = "$tree_sha"
git -C "$repo" worktree remove --force "$V2_RESUME_WORKTREE" >/dev/null
rmdir "$V2_RESUME_PARENT"
printf 'POSITIVE_OK V2 resume reapplies immutable public bytes on divergent main\n'
"""

result = subprocess.run(
    ["wsl.exe", "-d", "Ubuntu", "--", "bash", "-s"],
    # Windows text mode rewrites LF to CRLF before bash reads stdin, turning
    # `pipefail` into `pipefail\r`.  Bytes preserve the shipped shell text.
    input=script.encode("utf-8"),
    capture_output=True,
    timeout=90,
)
stdout = result.stdout.decode("utf-8", "replace")
stderr = result.stderr.decode("utf-8", "replace")
if result.returncode != 0:
    raise SystemExit(
        f"V2 resume integration failed rc={result.returncode}\n"
        f"stdout:\n{stdout}\nstderr:\n{stderr}"
    )
print(stdout.strip())
