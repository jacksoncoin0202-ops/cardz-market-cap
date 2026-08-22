"""Call site for scripts/ensure_chrome_cdp.ps1 identity gate.

A check with zero callers is not a check. This invokes -SelfTest, which
plants Headless/Linux fixtures and asserts they reject, and refuses 9222.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PS1 = ROOT / "scripts" / "ensure_chrome_cdp.ps1"
sys.path.insert(0, str(ROOT / "pipelines"))
from cdp_identity import (  # noqa: E402
    fetch_targets,
    reject_reason,
    require_headed_windows,
    require_session_ready,
)


def _windows_path(path: Path) -> str:
    if sys.platform == "win32":
        return str(path)
    return subprocess.run(
        ["wslpath", "-w", str(path)],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    ).stdout.strip()


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    # Run from WSL and powershell.exe cannot open a /mnt/c/... -File path, nor
    # inherit a translated cwd; it then answers in the console codepage, which
    # is not UTF-8. Hand it a Windows path and decode leniently.
    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            _windows_path(PS1),
            *extra,
        ],
        cwd=str(ROOT) if sys.platform == "win32" else "/mnt/c/Windows/System32",
        capture_output=True,
        text=True,
        errors="replace",
        timeout=60,
    )


def test_selftest_rejects_headless_and_linux():
    result = _run("-SelfTest")
    out = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, out
    assert "SELFTEST_OK reject-wsl-headless" in out, out
    assert "SELFTEST_OK reject-linux" in out, out
    assert "SELFTEST_OK accept-windows-headed" in out, out
    assert "SELFTEST_OK reject-windows-headless" in out, out
    assert "SELFTEST_PASSED" in out, out


def test_port_9222_is_refused():
    result = _run("-Port", "9222")
    out = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 1, out
    assert "CDP_REFUSED port=9222" in out, out


def test_script_does_not_assign_automatic_pid():
    text = PS1.read_text(encoding="utf-8")
    assert "foreach ($pid " not in text
    assert "foreach ($PID " not in text


def test_script_forces_ipv4_debug_bind():
    text = PS1.read_text(encoding="utf-8")
    assert "--remote-debugging-address=127.0.0.1" in text


def test_python_identity_matches_ps1_fixtures():
    headless = {
        "Browser": "Chrome/146.0.7680.75",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/146.0.0.0 Safari/537.36",
    }
    linux = {
        "Browser": "Chrome/146.0.7680.75",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    }
    windows = {
        "Browser": "Chrome/146.0.7680.75",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
    }
    windows_headless = {
        "Browser": "Chrome/146.0.7680.75",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) HeadlessChrome/146.0.0.0 Safari/537.36",
    }
    assert reject_reason(headless) == "linux-or-wsl"
    assert reject_reason(linux) == "linux-or-wsl"
    assert reject_reason(windows) is None
    assert reject_reason(windows_headless) == "headless"
    assert reject_reason(None) == "no-listener"


def test_python_refuses_9222():
    try:
        require_headed_windows(9222)
    except RuntimeError as exc:
        assert "9222" in str(exc)
    else:
        raise AssertionError("9222 must raise")
    try:
        require_session_ready(9222)
    except RuntimeError as exc:
        assert "9222" in str(exc)
        return
    raise AssertionError("9222 session_ready must raise")


def test_dead_port_targets_are_none():
    assert fetch_targets(1, timeout=0.3) is None
    try:
        require_session_ready(1)
    except RuntimeError as exc:
        assert "no-listener" in str(exc) or "jammed-targets" in str(exc)
        return
    raise AssertionError("dead port must raise")


def test_supervisor_uses_identity_gate():
    text = (ROOT / "scripts" / "pc_full900_supervisor.ps1").read_text(encoding="utf-8")
    assert "-IdentityOnly" in text
    assert 'StatusCode -eq 200' not in text


def test_preflight_cdp_budget_covers_evict():
    text = (ROOT / "scripts" / "preflight_daily_chain.ps1").read_text(encoding="utf-8")
    assert '"$CdpPort") 90' in text


def test_fetchers_refuse_wrong_identity():
    cf = (ROOT / "pipelines" / "pricecharting_cf_session.py").read_text(encoding="utf-8")
    shard = (ROOT / "pipelines" / "pc_full_shard_runner.py").read_text(encoding="utf-8")
    sold = (ROOT / "pipelines" / "pc_cdp_sold_refresh_win.py").read_text(encoding="utf-8")
    assert "reject_reason" in cf
    assert "require_session_ready" in cf
    assert "reject_reason" in shard
    assert "require_session_ready" in sold
    assert "urlopen(f\"http://127.0.0.1:{port}/json/version\"" not in shard
    assert "jammed-targets" in PS1.read_text(encoding="utf-8")
    assert "json/list" in PS1.read_text(encoding="utf-8")
    assert "curl.exe" in PS1.read_text(encoding="utf-8")


def test_live_9333_is_windows_headed_or_absent():
    reason = reject_reason(__import__("cdp_identity").fetch_version(9333))
    assert reason in (None, "no-listener"), reason


if __name__ == "__main__":
    tests = [
        test_selftest_rejects_headless_and_linux,
        test_port_9222_is_refused,
        test_script_does_not_assign_automatic_pid,
        test_script_forces_ipv4_debug_bind,
        test_python_identity_matches_ps1_fixtures,
        test_python_refuses_9222,
        test_dead_port_targets_are_none,
        test_supervisor_uses_identity_gate,
        test_preflight_cdp_budget_covers_evict,
        test_fetchers_refuse_wrong_identity,
        test_live_9333_is_windows_headed_or_absent,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
    sys.exit(1 if failed else 0)
