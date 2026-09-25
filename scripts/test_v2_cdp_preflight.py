"""Windows preflight regression: real sleeping helper, mocked browser/launcher IO.

Run from Windows or WSL: python scripts/test_v2_cdp_preflight.py
Mutations run only in temporary copies; no real WSL, Chrome or tick is invoked.
"""
from __future__ import annotations

import base64
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENSURE = ROOT / "scripts/ensure_chrome_cdp.ps1"
LAUNCHER = ROOT / "scripts/cardz_daily_v2_launcher.ps1"


def winpath(path):
    if sys.platform == "win32":
        return str(path)
    return subprocess.check_output(["wslpath", "-w", str(path)], text=True).strip()


def ps(code, timeout=20):
    encoded = base64.b64encode(code.encode("utf-16le")).decode()
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
        capture_output=True, text=True, errors="replace", timeout=timeout,
    )


def quote(value):
    return "'" + str(value).replace("'", "''") + "'"


class PreflightTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="cdp-preflight-test-", dir=ROOT / "logs")
        cls.folder = Path(cls.temp.name)
        cls.exe = cls.folder / "wsl.exe"
        # A native process, not a mocked WaitForExit. Record only our own PIDs.
        result = ps(r"""
$ErrorActionPreference = 'Stop'
Add-Type -OutputAssembly EXE_PATH -OutputType ConsoleApplication -TypeDefinition @'
using System;
using System.IO;
using System.Diagnostics;
using System.Threading;
public class FakeWsl {
    public static int Main(string[] args) {
        File.AppendAllText(Environment.GetEnvironmentVariable("CARDZ_TEST_WSL_LOG"),
            Process.GetCurrentProcess().Id + " " + String.Join(" ", args) + "\n");
        if (Environment.GetEnvironmentVariable("CARDZ_TEST_WSL_MODE") == "hang")
            Thread.Sleep(120000);
        return 2;
    }
}
'@
""".replace("EXE_PATH", quote(winpath(cls.exe))))
        if result.returncode:
            cls.temp.cleanup()
            raise AssertionError(result.stdout + result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def revive(self, reason, mode="hang", mutant=False):
        source = ENSURE.read_text(encoding="utf-8-sig")
        functions, main = source[source.index("function Get-CdpVersion"):].split(
            "\nif ($Port -eq 9222) {", 1)
        if mutant:
            self.assertEqual(functions.count("$p.WaitForExit(3000)"), 1)
            functions = functions.replace("$p.WaitForExit(3000)", "$p.WaitForExit()")
        record = self.folder / "helpers.log"
        record.write_text("")
        harness = self.folder / "revive.ps1"
        fixture = r'''
$Port = 9333
$UserDataDir = 'fixture-cardz-chrome-cdp-9333'
$StartUrl = 'about:blank'
$ErrorActionPreference = 'Stop'
$env:PATH = FAKE_DIR + ';' + $env:PATH
$env:CARDZ_TEST_WSL_LOG = RECORD
$env:CARDZ_TEST_WSL_MODE = FIXTURE_MODE
'''.replace("FAKE_DIR", quote(winpath(self.folder))).replace("RECORD", quote(winpath(record))).replace("FIXTURE_MODE", quote(mode))
        mocks = r'''
$script:revived = $false
function netstat { return @() }
function Get-CdpVersion {
  if (-not $script:revived -and REASON -eq 'no-listener') { return $null }
  $ua = 'Windows NT 10.0'
  if (-not $script:revived -and REASON -eq 'linux-or-wsl') { $ua = 'Linux X11' }
  return [pscustomobject]@{ Browser = 'Chrome/146'; 'User-Agent' = $ua }
}
function Test-CdpTargetsReady { return ($script:revived -or REASON -ne 'jammed-targets') }
function Start-CardzChrome {
  param($CandidatePort, $ProfileDir, $Url)
  $script:revived = $true
  Write-Host "FIXTURE_START_CHROME port=$CandidatePort profile=$ProfileDir"
  return $true
}
'''.replace("REASON", quote(reason))
        # Only eviction-completion polling needs an absent listener after cleanup.
        # Keep real initial classification and the real eviction -> startup call site.
        functions = functions.replace(
            'if ($null -eq (Get-CdpVersion -CandidatePort $CandidatePort)) { return }',
            'return # fixture: listener removed')
        harness.write_text(fixture + functions + mocks + "\nif ($Port -eq 9222) {" + main, encoding="utf-8-sig")
        started = time.monotonic()
        try:
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", winpath(harness)],
                capture_output=True, text=True, errors="replace", timeout=12,
            )
            elapsed = time.monotonic() - started
            out = result.stdout + result.stderr
            self.assertEqual(result.returncode, 0, out)
            self.assertIn("FIXTURE_START_CHROME port=9333", out)
            marker = "CDP_EVICT_WSL_TIMEOUT" if mode == "hang" else "CDP_EVICT_WSL_FAILED"
            self.assertEqual(result.stdout.count(marker), 2, out)
            calls = record.read_text().splitlines()
            self.assertEqual(len(calls), 2, calls)
            self.assertIn("-d Ubuntu -- pkill -f -- --remote-debugging-port=9333", calls[0])
            self.assertIn("-d Ubuntu -- pkill -9 -f -- --remote-debugging-port=9333", calls[1])
            pids = ",".join(line.split()[0] for line in calls)
            alive = ps(f"@(Get-Process -Id {pids} -ErrorAction SilentlyContinue).Count")
            self.assertEqual(alive.stdout.strip(), "0", alive.stdout + alive.stderr)
            print(f"REVIVE_OK reason={reason} mode={mode} elapsed={elapsed:.2f}s helpers_exited=2", flush=True)
        finally:
            # Also reap the planted uncapped helper after the 12 s test watchdog.
            pids = ",".join(line.split()[0] for line in record.read_text().splitlines())
            if pids:
                ps(f"Stop-Process -Id {pids} -Force -ErrorAction SilentlyContinue")

    def test_hung_wsl_reaches_chrome_for_each_reject_reason(self):
        for reason in ("no-listener", "jammed-targets", "linux-or-wsl"):
            with self.subTest(reason=reason):
                self.revive(reason)

    def test_failed_wsl_reaches_chrome_for_each_reject_reason(self):
        for reason in ("no-listener", "jammed-targets", "linux-or-wsl"):
            with self.subTest(reason=reason):
                self.revive(reason, mode="fail")

    def test_planted_uncapped_wait_is_caught(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            self.revive("no-listener", mutant=True)
        print("MUTATION_CAUGHT uncapped WaitForExit: no Chrome startup within 12s", flush=True)

    def test_launcher_logs_child_output_before_throw(self):
        source = LAUNCHER.read_text(encoding="utf-8-sig")
        block = source[source.index('if ($SelfTest) {\n    Write-Log "SELFTEST_SKIP_PREFLIGHT'):].split("\nfunction Convert-ToWslPath", 1)[0]
        log_function = source[source.index("function Write-Log {"):].split("\nfunction Write-LauncherAlertLog", 1)[0]
        for code, output in ((1, "CDP_EMPTY\nCDP_EVICT_WSL_TIMEOUT\nCDP_REVIVE_FAILED"),
                             (124, "timeout 120000ms"), (1, "CDP_JAMMED " + "x" * 9000)):
            log = self.folder / "launcher.log"
            log.write_text("")
            setup = "$ErrorActionPreference = 'Stop'\n$LogPath = " + quote(winpath(log)) + "\n"
            setup += "$PSScriptRoot = " + quote(winpath(self.folder)) + "\n"
            setup += log_function + "\nfunction Invoke-HiddenPowerShellFile { return @{ ExitCode = " + str(code) + "; Output = " + quote(output) + " } }\n"
            def run(candidate):
                return ps(setup + '\ntry {\n' + candidate + '\n} catch { Write-Host "FIXTURE_THROW $($_.Exception.Message)" }')
            result = run(block)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn("FIXTURE_THROW CARDZ CDP 9333 preflight failed", result.stdout)
            logged = log.read_text(encoding="utf-8-sig")
            self.assertIn("CARDZ_V2_PREFLIGHT_OUTPUT cdp=9333 " + output[:8000], logged)
            if len(output) > 8000:
                self.assertIn(" [truncated]", logged)
                self.assertNotIn(output, logged)
            # Plant the original missing-log bug in this same executed block.
            needle = '        Write-Log ("CARDZ_V2_PREFLIGHT_OUTPUT cdp=9333 " + $cdpOutput)'
            self.assertEqual(block.count(needle), 1)
            log.write_text("")
            run(block.replace(needle, ""))
            with self.assertRaises(AssertionError):
                self.assertIn("CARDZ_V2_PREFLIGHT_OUTPUT cdp=9333 " + output[:8000],
                              log.read_text(encoding="utf-8-sig"))
        print("LAUNCHER_OUTPUT_OK exit=1/124/truncated; MUTATION_CAUGHT missing child-output log", flush=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
