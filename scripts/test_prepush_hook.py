#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/githooks/cardz_prepush.py: what refuses a push, and that it fires.

Every rule is planted: a fixture built at runtime (so this file never holds a
real pattern) must hit, a near miss must not, and removing the rule must turn
its fixture green again. The gate classifier and the secret scan run against a
real temporary git repo; the WSL suite itself is swapped for a stub in-process,
never through an environment switch a push could set.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

sys.dont_write_bytecode = True  # scripts/githooks/ is installed file by file
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts" / "githooks"))
import cardz_prepush as H  # noqa: E402

FAILED: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        print(f"ok   {label}")
    else:
        FAILED.append(label)
        print(f"FAIL {label}{': ' + detail if detail else ''}")


# 1. Token rules: fixture hits, near miss does not, removing the rule unhits.
J = "".join
FIXTURES = {
    "private-key": (J(["-----BEGIN ", "RSA PRIVATE", " KEY-----"]), J(["-----BEGIN ", "PUBLIC KEY-----"])),
    "aws-key-id": (J(["AK", "IA", "ABCDEFGHIJKLMNOP"]), J(["AK", "IA", "ABCDEFGHIJKLMNO"])),
    "github-token": (J(["gh", "p_", "a" * 36]), J(["gh", "p_", "a" * 20])),
    "anthropic-key": (J(["sk-", "ant-", "b" * 24]), J(["sk-", "ant-", "b" * 5])),
    "openai-key": (J(["sk-", "proj-", "c" * 40]), J(["sk-", "c" * 10])),
    "slack-token": (J(["xo", "xb-", "1234567890ab"]), J(["xo", "xb-", "12"])),
    "google-api-key": (J(["AI", "za", "d" * 35]), J(["AI", "za", "d" * 20])),
    "telegram-bot-token": (J(["123456789", ":AA", "e" * 33]), J(["123456789", ":AB", "e" * 33])),
    "stripe-live": (J(["sk", "_live_", "f" * 24]), J(["sk", "_test_", "f" * 24])),
}
check("every token rule has a fixture", set(FIXTURES) == set(H.TOKEN_RULES), str(set(H.TOKEN_RULES) ^ set(FIXTURES)))
for rule, (hit, miss) in FIXTURES.items():
    found = H.scan_text(f"x = '{hit}'\n", "f.txt", "0" * 40, [])
    check(f"{rule}: fixture hits", any(f"rule={rule} " in f for f in found), str(found))
    check(f"{rule}: near miss stays quiet", not H.scan_text(f"x = '{miss}'\n", "f.txt", "0" * 40, []))
    check(f"{rule}: the finding never carries the value", all(hit not in f for f in found))
    saved = H.COMPILED.pop(rule)
    try:
        check(f"N({rule}): without the rule the fixture is not caught",
              not any(f"rule={rule} " in f for f in H.scan_text(hit, "f.txt", "0" * 40, [])))
    finally:
        H.COMPILED[rule] = saved

# 2. Paths that must never be pushed, and ones that must pass.
for path in ("data/runtime/config/backend.env", "sub/.env.local", ".env", "data/private/x.json",
             "data/runtime/x", "scripts/promo_destinations.json", "keys/id_ed25519", "x.pem"):
    check(f"deny path {path}", bool(H.DENY_PATH.search(path)))
for path in (".env.example", "data/public/seed-snapshot.json", "docs/backend.env.md", "scripts/test_env.py"):
    check(f"allow path {path}", not H.DENY_PATH.search(path))

# 3. The release's gate line is read, not copied.
release_sh = (ROOT / "scripts" / "daily_public_release.sh").read_text(encoding="utf-8")
chain_test = (ROOT / "scripts" / "test_autonomous_release_chain.py").read_text(encoding="utf-8")


def contract_of(text: str):
    pythons, arguments = H.TEST_PY_RE.findall(text), H.GATE_RE.findall(text)
    if len(pythons) != 1 or len(arguments) != 1:
        raise H.Refuse("ambiguous")
    return pythons[0], arguments[0].split()


py, args = contract_of(release_sh)
check("gate python is the release's TEST_PY", py in chain_test and py.endswith("/.venv-backend/bin/python"), py)
check("gate args are the release's --no-db line", args == ["--no-db", "--skip-fe"], str(args))
check("the --release-db-gates line is never the hook's gate (needs the live DB)",
      "--release-db-gates" in release_sh and "--release-db-gates" not in args)
gate_line = next(line for line in release_sh.splitlines() if line.endswith(" --no-db --skip-fe") and "SOURCE_REPO" in line)
for label, planted in (
    ("TEST_PY removed", release_sh.replace('TEST_PY="' + py + '"', "")),
    ("gate line duplicated", release_sh.replace(gate_line, gate_line + "\n" + gate_line)),
):
    try:
        contract_of(planted)
    except H.Refuse:
        check(f"N(gate contract): {label} refuses", True)
    else:
        check(f"N(gate contract): {label} refuses", False, "parsed anyway")
moved = release_sh.replace('TEST_PY="' + py + '"', 'TEST_PY="/opt/other/python"')
check("gate python follows the script, not a constant", contract_of(moved)[0] == "/opt/other/python")

# 4. Hook text contract.
hook = (ROOT / "scripts" / "githooks" / "pre-push").read_bytes().decode("utf-8")
NEEDLES = ["cardz-githook:", 'MINGW*|MSYS*|CYGWIN*) ;;', 'cardz_prepush.py" "$@" < "$REFS"',
           'git lfs pre-push "$@" < "$REFS"\n', "exit 1"]


def hook_ok(text: str) -> bool:
    return (all(n in text for n in NEEDLES) and "\r" not in text
            and text.rstrip("\n").splitlines()[-1] == 'git lfs pre-push "$@" < "$REFS"'
            and text.count('< "$REFS"') == 2)


check("pre-push hook text contract", hook_ok(hook))
for needle in NEEDLES:
    check(f"N(hook): without {needle!r} the contract fails", not hook_ok(hook.replace(needle, "")))


# 5. A real repo: classifier, scan, and refusal, with the WSL run stubbed.
def sh(cwd: Path, *argv: str) -> str:
    return subprocess.run(argv, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo: Path, rel: str, text: str, message: str = "c") -> str:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    sh(repo, "git", "add", rel)
    sh(repo, "git", "commit", "-q", "-m", message)
    return sh(repo, "git", "rev-parse", "HEAD")


with tempfile.TemporaryDirectory() as tmp:
    base = Path(tmp)
    remote, repo = base / "remote.git", base / "work"
    sh(base, "git", "init", "-q", "--bare", str(remote))
    sh(base, "git", "init", "-q", "-b", "main", str(repo))
    for key, value in (("user.email", "t@example.invalid"), ("user.name", "t"), ("commit.gpgsign", "false"),
                       ("core.hooksPath", str(base / "nohooks"))):
        sh(repo, "git", "config", key, value)
    sh(repo, "git", "remote", "add", "origin", str(remote))
    secret = J(["cardz", "-planted-", "literal-", "9f3a"])
    config = repo / "data" / "runtime" / "config"
    config.mkdir(parents=True)
    (config / "backend.env").write_text(f"# local\nCARDZ_DB_PASSWORD={secret}\nCARDZ_DB_HOST=127.0.0.1\n", encoding="utf-8")
    (repo / ".gitignore").write_text("data/runtime/\n", encoding="utf-8")
    first = commit(repo, "scripts/daily_public_release.sh", release_sh)
    sh(repo, "git", "push", "-q", "origin", "main")
    sh(repo, "git", "fetch", "-q", "origin")

    old_cwd = os.getcwd()
    os.chdir(repo)
    gate_calls: list[str] = []
    real_run_gate, real_cache_valid = H.run_gate, H.cache_valid
    H.cache_valid = lambda *_a: False
    H.write_cache = lambda *_a: None
    try:
        check("literal values come from the env file", secret in H.literal_values())

        def push_line(sha: str, ref: str = "refs/heads/main") -> str:
            return f"refs/heads/x {sha} {ref} {first}\n"

        def main_with(stdin: str, verdict: str | None) -> tuple[int, str]:
            gate_calls.clear()
            H.run_gate = lambda sha, _py, _args: (gate_calls.append(sha), verdict)[1]
            err = io.StringIO()
            with redirect_stderr(err):
                code = H.main(stdin)
            return code, err.getvalue()

        pipeline = commit(repo, "pipelines/x.py", "print(1)\n")
        code, _ = main_with(push_line(pipeline), "CARDZ_TEST_RESULT {}")
        check("main + pipelines change runs the gate", code == 0 and gate_calls == [pipeline], str(gate_calls))
        code, err = main_with(push_line(pipeline), None)
        check("a failing gate refuses the push", code == 1 and "WSL gate failed" in err, err)
        code, _ = main_with(push_line(pipeline, "refs/heads/rebuild/036-foundation"), None)
        check("a non-main branch is not gated", code == 0 and not gate_calls)
        sh(repo, "git", "reset", "-q", "--hard", first)

        public = commit(repo, "data/public/seed-snapshot.json", "{}\n")
        code, _ = main_with(push_line(public), None)
        check("data/public-only change skips the gate", code == 0 and not gate_calls)
        attrs = commit(repo, ".gitattributes", "*.sh text eol=lf\n")
        code, _ = main_with(push_line(attrs), "CARDZ_TEST_RESULT {}")
        check(".gitattributes change is gated", gate_calls == [attrs])
        sh(repo, "git", "reset", "-q", "--hard", first)

        check("a delete is ignored", H.parse_updates(f"(delete) {H.ZERO} refs/heads/main {first}\n") == [])

        leaked = commit(repo, "docs/note.md", f"password is {secret}\n")
        code, err = main_with(push_line(leaked), "CARDZ_TEST_RESULT {}")
        check("a planted literal refuses the push before the gate", code == 1 and not gate_calls and "rule=literal" in err, err)
        check("the refusal never prints the literal", secret not in err)
        sh(repo, "git", "reset", "-q", "--hard", first)

        token = commit(repo, "a.txt", "ok\n", message="deploy note " + FIXTURES["github-token"][0])
        code, err = main_with(push_line(token), "CARDZ_TEST_RESULT {}")
        check("a token in a commit message refuses the push", code == 1 and "where=commit-message" in err, err)
        sh(repo, "git", "reset", "-q", "--hard", first)

        env_file = commit(repo, "apps/web/.env.local", "X=1\n")
        code, err = main_with(push_line(env_file), "CARDZ_TEST_RESULT {}")
        check("a denied path refuses the push", code == 1 and "rule=path" in err, err)
        sh(repo, "git", "reset", "-q", "--hard", first)

        (config / "backend.env").unlink()
        try:
            H.literal_values()
        except H.Refuse:
            check("no literal source fails closed", True)
        else:
            check("no literal source fails closed", False)
    finally:
        H.run_gate, H.cache_valid = real_run_gate, real_cache_valid
        os.chdir(old_cwd)

print()
if FAILED:
    print(f"{len(FAILED)} FAILED: {FAILED}")
    raise SystemExit(1)
print("pre-push hook rules hold")
