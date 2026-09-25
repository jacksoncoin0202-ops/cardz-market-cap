#!/usr/bin/env python3
# cardz-githook: pre-push-orchestrator
"""What scripts/githooks/pre-push runs before a push leaves this machine.

stdin: '<local ref> <local sha> <remote ref> <remote sha>' lines; argv: remote
name and url. Exit != 0 refuses the push.

1. Secret scan on every pushed ref: new blobs, commit messages and paths. A
   finding prints rule, place, length and a hash prefix -- never the value --
   and there is no allow pragma: a false positive is fixed in the rule.
2. refs/heads/main with any change outside data/public/ runs the WSL test
   suite on a clean export of the exact pushed sha. TEST_PY and the arguments
   are read from that sha's scripts/daily_public_release.sh (the release's own
   gate), so hook and release cannot drift apart.

The rule "run the WSL gate before pushing pipeline changes" lived only in
memory until 2026-09-26, and 623b34b7 went out green on Windows and red on WSL.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ZERO = "0" * 40
GATED_REF = "refs/heads/main"
UNGATED_PREFIX = "data/public/"
CACHE_TTL = 24 * 3600
GATE_TIMEOUT = 1800
MAX_SCAN_BYTES = 64 * 1024 * 1024
# git archive applies autocrlf and LFS smudge by default; the gate must see the
# committed bytes, the same ones the release clone checks out on ext4.
EXPORT = [
    "-c", "core.autocrlf=false", "-c", "core.eol=lf",
    "-c", "filter.lfs.process=", "-c", "filter.lfs.smudge=", "-c", "filter.lfs.required=false",
]
DENY_PATH = re.compile(
    r"(^|/)(backend\.env|\.env(?!\.example$)[^/]*|id_(rsa|ed25519|ecdsa)[^/]*|[^/]+\.(pem|p12|pfx))$"
    r"|^data/(private|runtime)/|^scripts/promo_destinations\.json$"
)
TOKEN_RULES = {
    "private-key": r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----",
    "aws-key-id": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "github-token": r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})",
    "anthropic-key": r"\bsk-ant-[A-Za-z0-9_-]{20,}",
    "openai-key": r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}",
    "slack-token": r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    "google-api-key": r"\bAIza[0-9A-Za-z_-]{35}",
    "telegram-bot-token": r"\b\d{8,10}:AA[0-9A-Za-z_-]{33}\b",
    "stripe-live": r"\b[sr]k_live_[0-9A-Za-z]{20,}",
}
LITERAL_KEY = re.compile(r"(PASS|SECRET|TOKEN|KEY)", re.I)
LITERAL_MIN = 8
TEST_PY_RE = re.compile(r'^TEST_PY="(/[^"]+)"$', re.M)
# Only the --no-db line. The --release-db-gates line needs the live DB, which a
# clean export does not have.
GATE_RE = re.compile(
    r'^"\$TEST_PY" -X utf8 "\$SOURCE_REPO/scripts/run_all_tests\.py" (--no-db(?: --[a-z-]+)*)$',
    re.M,
)
RESULT_PREFIX = "CARDZ_TEST_RESULT "
WSL_GATE = r'''set -euo pipefail
sha=$1; py=$2; shift 2
test -x "$py" || { echo "cardz pre-push: missing $py" >&2; exit 97; }
root="$HOME/.cache/cardz-prepush"; mkdir -p "$root"
find "$root" -mindepth 1 -maxdepth 1 -mmin +1440 -exec rm -rf {} +
d=$(mktemp -d "$root/${sha:0:12}.XXXXXX"); trap 'rm -rf "$d"' EXIT
tar -x -C "$d"; cd "$d"
"$py" -X utf8 scripts/run_all_tests.py "$@"
'''


class Refuse(Exception):
    pass


def eprint(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def git(*args: str, data: bytes | None = None, binary: bool = False):
    proc = subprocess.run(["git", *args], input=data, capture_output=True)
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()[:300]
        raise Refuse(f"git {args[0]} failed: {detail}")
    return proc.stdout if binary else proc.stdout.decode("utf-8", "replace")


def commit_exists(sha: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], capture_output=True).returncode == 0


def parse_updates(text: str) -> list[tuple[str, str, str, str]]:
    updates = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 4:
            continue
        lref, lsha, rref, rsha = parts
        if lsha == ZERO or lsha == rsha:
            continue
        updates.append((lref, lsha, rref, rsha))
    return updates


def excludes(rsha: str) -> list[str]:
    out = ["--not", "--remotes=origin"]
    if rsha != ZERO and commit_exists(rsha):
        out.append(rsha)
    return out


def common_dir() -> Path:
    return Path(git("rev-parse", "--git-common-dir").strip()).resolve()


def literal_values() -> list[str]:
    """Secret values this machine holds, read from where they live. Fail closed."""

    common = common_dir()
    roots = {Path(git("rev-parse", "--show-toplevel").strip()).resolve(), common.parent}
    values: set[str] = set()
    sources = 0
    for root in sorted(roots):
        config = root / "data" / "runtime" / "config"
        if not config.is_dir():
            continue
        for env in sorted(config.glob("*.env")):
            sources += 1
            for line in env.read_text(encoding="utf-8", errors="replace").splitlines():
                key, sep, value = line.partition("=")
                if not sep or key.strip().startswith("#") or not LITERAL_KEY.search(key):
                    continue
                value = value.strip().strip("\"'")
                if len(value) >= LITERAL_MIN:
                    values.add(value)
    extra = common / "cardz-secret-literals"
    if extra.is_file():
        sources += 1
        for line in extra.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and len(line) >= LITERAL_MIN:
                values.add(line)
    if not sources:
        raise Refuse("no secret literal source (data/runtime/config/*.env or <git-common>/cardz-secret-literals)")
    return sorted(values)


COMPILED = {rule: re.compile(rx) for rule, rx in TOKEN_RULES.items()}


def finding(rule: str, where: str, commit: str, value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()[:8]
    return f"secret: rule={rule} where={where} commit={commit[:12]} len={len(value)} h={digest}"


def scan_text(text: str, where: str, commit: str, literals: list[str]) -> list[str]:
    out = []
    for rule, rx in COMPILED.items():
        for match in rx.finditer(text):
            out.append(finding(rule, where, commit, match.group(0)))
    for literal in literals:
        if literal in text:
            out.append(finding("literal", where, commit, literal))
    return out


def _read_objects(names: list[str]) -> dict[str, tuple[str, bytes]]:
    if not names:
        return {}
    raw = git("cat-file", "--batch", data=("\n".join(names) + "\n").encode(), binary=True)
    out: dict[str, tuple[str, bytes]] = {}
    pos = 0
    while pos < len(raw):
        end = raw.index(b"\n", pos)
        header = raw[pos:end].decode("utf-8", "replace").split()
        pos = end + 1
        if len(header) < 3:  # "<name> missing"
            continue
        size = int(header[2])
        out[header[0]] = (header[1], raw[pos:pos + size])
        pos += size + 1
    return out


def scan(updates: list[tuple[str, str, str, str]]) -> list[str]:
    literals = literal_values()
    found: list[str] = []
    for _lref, lsha, _rref, rsha in updates:
        tail = excludes(rsha)
        entries = []
        for line in git("rev-list", "--objects", lsha, *tail).splitlines():
            name, _, path = line.partition(" ")
            if path:
                entries.append((name, path))
        objects = _read_objects(sorted({name for name, _ in entries}))
        for name, path in entries:
            kind, body = objects.get(name, ("", b""))
            if kind != "blob":
                continue
            if DENY_PATH.search(path):
                found.append(f"secret: rule=path where={path} commit={lsha[:12]}")
            if len(body) > MAX_SCAN_BYTES or b"\0" in body[:8000]:
                continue
            found.extend(scan_text(body.decode("utf-8", "replace"), path, lsha, literals))
        log = git("log", "--format=%H%x00%B%x1e", lsha, *tail)
        for record in log.split("\x1e"):
            commit, sep, message = record.strip("\n").partition("\x00")
            if sep:
                found.extend(scan_text(message, "commit-message", commit, literals))
    return found


def gate_base(lsha: str, rsha: str) -> str | None:
    if rsha != ZERO and commit_exists(rsha):
        return rsha
    proc = subprocess.run(
        ["git", "merge-base", lsha, "refs/remotes/origin/main"], capture_output=True, text=True
    )
    return proc.stdout.strip() or None if proc.returncode == 0 else None


def needs_gate(lsha: str, rref: str, rsha: str) -> bool:
    if rref != GATED_REF:
        return False
    base = gate_base(lsha, rsha)
    if base is None:
        return True
    changed = git("diff", "--name-only", "--no-renames", "-z", base, lsha).split("\0")
    return any(path and not path.startswith(UNGATED_PREFIX) for path in changed)


def gate_contract(sha: str) -> tuple[str, list[str]]:
    source = git("show", f"{sha}:scripts/daily_public_release.sh")
    pythons = TEST_PY_RE.findall(source)
    arguments = GATE_RE.findall(source)
    if len(pythons) != 1 or len(arguments) != 1:
        raise Refuse(
            "cannot read one TEST_PY and one --no-db gate line from scripts/daily_public_release.sh "
            f"({len(pythons)} TEST_PY, {len(arguments)} gate lines)"
        )
    return pythons[0], arguments[0].split()


def identity(py: str, args: list[str]) -> str:
    here = Path(__file__).resolve()
    hook = here.parent / "pre-push"
    blob = here.read_bytes() + (hook.read_bytes() if hook.is_file() else b"")
    return hashlib.sha256(blob + (py + " " + " ".join(args)).encode()).hexdigest()


def cache_file(tree: str) -> Path:
    return common_dir() / "cardz-prepush" / f"pass-{tree}.json"


def cache_valid(tree: str, py: str, args: list[str]) -> bool:
    path = cache_file(tree)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return doc.get("identity") == identity(py, args) and time.time() - float(doc.get("at", 0)) < CACHE_TTL


def write_cache(tree: str, py: str, args: list[str], result: str) -> None:
    path = cache_file(tree)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"identity": identity(py, args), "at": time.time(), "result": result}
    path.write_text(json.dumps(doc) + "\n", encoding="utf-8")


def run_gate(sha: str, py: str, args: list[str]) -> str | None:
    """The CARDZ_TEST_RESULT line when the suite passed on WSL, else None."""

    env = dict(os.environ, MSYS_NO_PATHCONV="1", MSYS2_ARG_CONV_EXCL="*")
    archive = subprocess.Popen(["git", *EXPORT, "archive", "--format=tar", sha], stdout=subprocess.PIPE)
    wsl = subprocess.Popen(
        ["wsl.exe", "-d", "Ubuntu", "--exec", "/bin/bash", "-c", WSL_GATE, "cardz-prepush", sha, py, *args],
        stdin=archive.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    archive.stdout.close()
    timer = threading.Timer(GATE_TIMEOUT, wsl.kill)
    timer.start()
    result = None
    try:
        for raw in wsl.stdout:
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            eprint(line)
            if line.startswith(RESULT_PREFIX):
                result = line
        wsl.wait()
        archive.wait()
    finally:
        timer.cancel()
    if archive.returncode != 0 or wsl.returncode != 0 or result is None:
        return None
    verdict = json.loads(result[len(RESULT_PREFIX):])
    if verdict.get("failed") or verdict.get("timeouts"):
        return None
    return result


def main(stdin_text: str) -> int:
    updates = parse_updates(stdin_text)
    if not updates:
        return 0
    bad = scan(updates)
    if bad:
        eprint("\n".join(bad))
        eprint(f"cardz pre-push: {len(bad)} secret finding(s); values are never printed")
        return 1
    trees: dict[str, str] = {}
    for _lref, lsha, rref, rsha in updates:
        if needs_gate(lsha, rref, rsha):
            trees.setdefault(git("rev-parse", f"{lsha}^{{tree}}").strip(), lsha)
    for tree, sha in trees.items():
        py, args = gate_contract(sha)
        if cache_valid(tree, py, args):
            eprint(f"cardz pre-push: tree {tree[:12]} already passed the WSL gate")
            continue
        eprint(f"cardz pre-push: WSL gate on {sha[:12]} ({' '.join(args)}), about 5 minutes")
        result = run_gate(sha, py, args)
        if result is None:
            eprint(f"cardz pre-push: WSL gate failed on {sha[:12]}")
            return 1
        write_cache(tree, py, args, result)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.stdin.read()))
    except Refuse as exc:
        eprint(f"cardz pre-push: REFUSED ({exc})")
        raise SystemExit(2)
    except Exception as exc:  # noqa: BLE001 -- fail closed on anything unexpected
        eprint(f"cardz pre-push: REFUSED ({type(exc).__name__}: {exc})")
        raise SystemExit(2)
