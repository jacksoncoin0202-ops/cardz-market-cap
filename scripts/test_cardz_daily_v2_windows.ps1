# Windows-side durability proofs for the CARDZ Daily Chain V2 boundary.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test_cardz_daily_v2_windows.ps1
#
# Every check below is a proof-of-fire for something that actually broke on
# 2026-08-22: a visible Windows Terminal window killed 9 ticks (CTRL_CLOSE ->
# 0xC000013A) and nothing outside the chain noticed for hours.
# Uses only temp dirs; never touches data/runtime, the WSL journal or MySQL.
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Scripts = $PSScriptRoot
$Vbs = Join-Path $Scripts "cardz_silent_run.vbs"
$Launcher = Join-Path $Scripts "cardz_daily_v2_launcher.ps1"
$Watchdog = Join-Path $Scripts "watchdog_live_release.ps1"
$Installer = Join-Path $Scripts "install_cardz_daily_v2_task.ps1"
$WScriptExe = Join-Path ([Environment]::SystemDirectory) "wscript.exe"
$PsExe = Join-Path ([Environment]::SystemDirectory) "WindowsPowerShell\v1.0\powershell.exe"

$tmp = Join-Path $env:TEMP ("cardz-v2-wintest-" + [guid]::NewGuid().ToString("N").Substring(0, 12))
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$fails = 0

function Check([string]$name, [bool]$ok, [string]$detail) {
    if ($ok) { Write-Host "PASS $name :: $detail" }
    else { Write-Host "FAIL $name :: $detail"; $script:fails++ }
}

function Invoke-Capture([string]$exe, [string]$argLine) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $exe
    $psi.Arguments = $argLine
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [System.Diagnostics.Process]::Start($psi)
    $out = $p.StandardOutput.ReadToEnd()
    $err = $p.StandardError.ReadToEnd()
    $p.WaitForExit()
    return @{ Code = [int]$p.ExitCode; Out = ($out + $err) }
}

function Get-ConsoleCount { return @(Get-Process -Name OpenConsole -ErrorAction SilentlyContinue).Count }

# ---- (a) cardz_silent_run.vbs: rc propagates, no OpenConsole/WT window ------
$rcOk = $false
$consoleOk = $false
$before = 0; $after = 0; $rc = -1
for ($attempt = 1; $attempt -le 2; $attempt++) {
    $before = Get-ConsoleCount
    $r = Invoke-Capture $WScriptExe "//nologo //B `"$Vbs`" powershell.exe -NoProfile -NonInteractive -Command exit(7)"
    $after = Get-ConsoleCount
    $rc = $r.Code
    $rcOk = ($rc -eq 7)
    $consoleOk = ($before -eq $after)
    if ($rcOk -and $consoleOk) { break }
    Start-Sleep -Seconds 2
}
Check "vbs-rc-propagates" $rcOk "expected 7, got $rc"
Check "vbs-no-openconsole-window" $consoleOk "OpenConsole.exe before=$before after=$after (retry-tolerant; flaky only if a human opened a terminal in that second)"
$rNoArgs = Invoke-Capture $WScriptExe "//nologo //B `"$Vbs`""
Check "vbs-usage-rc-64" ($rNoArgs.Code -eq 64) "no-args rc=$($rNoArgs.Code)"

# ---- (b) launcher -SelfTest: never starts a tick ---------------------------
$logDir = Join-Path $tmp "launcher-log"
$provDir = Join-Path $tmp "launcher-prov"
$healthySelfheal = Join-Path $tmp "wsl-selfheal-healthy.ps1"
[System.IO.File]::WriteAllText(
    $healthySelfheal,
    "Write-Output 'SELFHEAL_STUB_OK'`r`nexit 0`r`n",
    (New-Object System.Text.UTF8Encoding($false))
)
$env:CARDZ_V2_LAUNCHER_LOG_DIR = $logDir
$env:CARDZ_V2_LAUNCHER_PROVENANCE_DIR = $provDir
$env:CARDZ_V2_WSL_SELFHEAL_PS1 = $healthySelfheal
$rSelf = Invoke-Capture $PsExe "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Launcher`" -SelfTest"
$rSelfMax = Invoke-Capture $PsExe "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Launcher`" -SelfTest -MaxRuntimeSeconds 1234"
$rSelfFlags = Invoke-Capture $PsExe ("-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Launcher`" -SelfTest" +
    " -AllowPublish -Notify -ManualE2E -RenewManualWindow -BusinessDate 2026-08-22")
Remove-Item env:CARDZ_V2_LAUNCHER_LOG_DIR -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_LAUNCHER_PROVENANCE_DIR -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_WSL_SELFHEAL_PS1 -ErrorAction SilentlyContinue

$logFiles = @(Get-ChildItem -Path $logDir -Filter "launcher-*.log" -ErrorAction SilentlyContinue)
$logText = ""
if ($logFiles.Count -gt 0) { $logText = (Get-Content -LiteralPath $logFiles[0].FullName -Raw) }
Check "launcher-selftest-exit0" ($rSelf.Code -eq 0) "exit=$($rSelf.Code)"
Check "launcher-selftest-prints-ok" ($rSelf.Out -match "SELFTEST_OK") "stdout head: $((($rSelf.Out -split "`n") | Select-Object -First 1))"
Check "launcher-selftest-log-created" ($logFiles.Count -eq 1) "log files under $logDir = $($logFiles.Count)"
Check "launcher-selftest-log-has-ok" ($logText -match "SELFTEST_OK") "log contains SELFTEST_OK"
Check "launcher-selftest-skipped-preflight" ($logText -match "SELFTEST_SKIP_PREFLIGHT") "log contains SELFTEST_SKIP_PREFLIGHT"
Check "launcher-selftest-no-tick" (-not ($logText -match "CARDZ_V2_START")) "log must not contain CARDZ_V2_START (no wsl.exe tick)"
Check "launcher-default-max-runtime-2100" ($logText -match "--max-runtime-seconds 2100") "default -MaxRuntimeSeconds lands in the wsl arg list"
Check "launcher-max-runtime-override" ($rSelfMax.Out -match "--max-runtime-seconds 1234") "-MaxRuntimeSeconds 1234 lands in the wsl arg list"
Check "launcher-auto-supersede-enters-wsl" ($logText -match "env CARDZ_V2_AUTO_SUPERSEDE=1 python3") "scheduled launcher owns the WSL auto-alignment flag"
$flagLine = (($rSelfFlags.Out -split "`r?`n") | Where-Object { $_ -match "CARDZ_V2_WSL_ARGS" } | Select-Object -First 1)
$flagsOk = ($flagLine -match "--allow-publish") -and ($flagLine -match "--notify") -and
           ($flagLine -match "--manual-e2e-window") -and ($flagLine -match "--renew-manual-e2e-window") -and
           ($flagLine -match "--business-date 2026-08-22")
Check "launcher-existing-flags-unchanged" $flagsOk "$flagLine"
Check "launcher-healthy-no-alert-artifact" (-not (Test-Path -LiteralPath (Join-Path $provDir "launcher-alerts"))) "healthy self-test must create no launcher alert artifact"
Check "launcher-healthy-no-notify" (-not ($logText -match "CARDZ_V2_LAUNCHER_ALERT_NOTIFY")) "healthy self-test must not invoke notifier"

# A dead WSL boundary must invoke the real notify_hermes alert contract, record
# its rc, and fall through to a Windows-native popup. Stubs isolate the proof
# from WSL, Telegram and the interactive desktop.
$deadLogDir = Join-Path $tmp "launcher-dead-log"
$deadProvDir = Join-Path $tmp "launcher-dead-prov"
$deadSelfheal = Join-Path $tmp "wsl-selfheal-dead.ps1"
$notifyHarness = Join-Path $tmp "notify-drop-harness.py"
$notifyReceipt = Join-Path $tmp "notify-drop-called.txt"
$notifyStateDir = Join-Path $tmp "notify-drop-state"
$popupStub = Join-Path $tmp "msg-stub.cmd"
$popupReceipt = Join-Path $tmp "popup-called.txt"
[System.IO.File]::WriteAllText(
    $deadSelfheal,
    "Write-Output 'SELFHEAL_STUB_DEAD'`r`nexit 3`r`n",
    (New-Object System.Text.UTF8Encoding($false))
)
$notifyHarnessBody = @'
import argparse
import importlib.util
import os
from pathlib import Path

real_path = Path(os.environ["CARDZ_V2_TEST_REAL_NOTIFY"])
spec = importlib.util.spec_from_file_location("cardz_notify_real", real_path)
module = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(module)
state_dir = Path(os.environ["CARDZ_V2_TEST_NOTIFY_STATE_DIR"])
module.STATE_PATH = state_dir / "state.json"
module.UNDELIVERED_DIR = state_dir / "undelivered"
module.send_message = lambda text: False
rc = module.cmd_alert(argparse.Namespace(
    key="v2-launcher-wsl-dead",
    text="dead WSL fixture",
    level="error",
    cooldown_min="0",
    require_delivery=True,
))
Path(os.environ["CARDZ_V2_TEST_NOTIFY_RECEIPT"]).write_text(str(rc), encoding="utf-8")
raise SystemExit(rc)
'@
[System.IO.File]::WriteAllText($notifyHarness, $notifyHarnessBody, (New-Object System.Text.UTF8Encoding($false)))
[System.IO.File]::WriteAllText(
    $popupStub,
    "@echo off`r`necho called> `"%CARDZ_V2_TEST_POPUP_RECEIPT%`"`r`nexit /b 0`r`n",
    (New-Object System.Text.UTF8Encoding($false))
)
$env:CARDZ_V2_LAUNCHER_LOG_DIR = $deadLogDir
$env:CARDZ_V2_LAUNCHER_PROVENANCE_DIR = $deadProvDir
$env:CARDZ_V2_WSL_SELFHEAL_PS1 = $deadSelfheal
$env:CARDZ_V2_NOTIFY_HERMES_PY = $notifyHarness
$env:CARDZ_V2_LAUNCHER_MSG_EXE = $popupStub
$env:CARDZ_V2_TEST_REAL_NOTIFY = Join-Path $Scripts "notify_hermes.py"
$env:CARDZ_V2_TEST_NOTIFY_STATE_DIR = $notifyStateDir
$env:CARDZ_V2_TEST_NOTIFY_RECEIPT = $notifyReceipt
$env:CARDZ_V2_TEST_POPUP_RECEIPT = $popupReceipt
$rDead = Invoke-Capture $PsExe "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Launcher`" -SelfTest"
Remove-Item env:CARDZ_V2_LAUNCHER_LOG_DIR -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_LAUNCHER_PROVENANCE_DIR -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_WSL_SELFHEAL_PS1 -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_NOTIFY_HERMES_PY -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_LAUNCHER_MSG_EXE -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_TEST_REAL_NOTIFY -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_TEST_NOTIFY_STATE_DIR -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_TEST_NOTIFY_RECEIPT -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_TEST_POPUP_RECEIPT -ErrorAction SilentlyContinue
$deadLogFiles = @(Get-ChildItem -Path $deadLogDir -Filter "launcher-*.log" -ErrorAction SilentlyContinue)
$deadLog = if ($deadLogFiles.Count -eq 1) { Get-Content -LiteralPath $deadLogFiles[0].FullName -Raw } else { "" }
$deadArtifacts = @(Get-ChildItem -Path (Join-Path $deadProvDir "launcher-alerts") -Filter "alert-*.json" -ErrorAction SilentlyContinue)
$undelivered = @(Get-ChildItem -Path (Join-Path $notifyStateDir "undelivered") -Filter "*.json" -ErrorAction SilentlyContinue)
Check "launcher-wsl-dead-exit3" ($rDead.Code -eq 3) "exit=$($rDead.Code)"
Check "launcher-wsl-dead-invokes-notifier" ($deadLog -match "CARDZ_V2_LAUNCHER_ALERT_NOTIFY key=v2-launcher-wsl-dead exit=\d+") "notify_hermes.py really ran and its rc was recorded"
Check "launcher-wsl-dead-fallback-on-drop" ($deadLog -match "CARDZ_V2_LAUNCHER_ALERT_POPUP key=v2-launcher-wsl-dead exit=0") "an undeliverable alert falls through to the Windows-native channel"
Check "launcher-wsl-dead-artifact" ($deadArtifacts.Count -eq 1) "Windows artifacts=$($deadArtifacts.Count)"
Check "launcher-wsl-dead-notify-drop-artifact" ($undelivered.Count -eq 1) "notify undelivered artifacts=$($undelivered.Count)"
Check "launcher-wsl-dead-real-notify-rc1" ((Test-Path -LiteralPath $notifyReceipt) -and ((Get-Content -LiteralPath $notifyReceipt -Raw).Trim() -eq "1")) "--require-delivery returned 1"
Check "launcher-wsl-dead-popup-process-ran" (Test-Path -LiteralPath $popupReceipt) "msg.exe-compatible stub was invoked"
Check "launcher-wsl-dead-no-tick" (-not ($deadLog -match "CARDZ_V2_START")) "dead WSL must not start a tick"

# Mutation proof: disable only the WSL-dead guard in a temp launcher copy. The
# same fixture must then lose exit=3 and the notifier call, proving the positive
# assertions above are attached to the shipped branch rather than test setup.
$mutationLauncher = Join-Path $tmp "cardz_daily_v2_launcher_mutated.ps1"
$launcherSource = [System.IO.File]::ReadAllText($Launcher, [System.Text.Encoding]::UTF8)
$mutationNeedle = 'if ($selfhealExit -ne 0) {'
Check "launcher-wsl-dead-mutation-anchor-unique" (([regex]::Matches($launcherSource, [regex]::Escape($mutationNeedle))).Count -eq 1) "mutation anchor must occur exactly once"
$mutatedSource = $launcherSource.Replace($mutationNeedle, 'if ($false -and $selfhealExit -ne 0) {')
[System.IO.File]::WriteAllText($mutationLauncher, $mutatedSource, (New-Object System.Text.UTF8Encoding($false)))
$mutationLogDir = Join-Path $tmp "launcher-mutation-log"
$mutationProvDir = Join-Path $tmp "launcher-mutation-prov"
$env:CARDZ_V2_LAUNCHER_LOG_DIR = $mutationLogDir
$env:CARDZ_V2_LAUNCHER_PROVENANCE_DIR = $mutationProvDir
$env:CARDZ_V2_WSL_SELFHEAL_PS1 = $deadSelfheal
$env:CARDZ_V2_NOTIFY_HERMES_PY = $notifyHarness
$env:CARDZ_V2_LAUNCHER_MSG_EXE = $popupStub
$env:CARDZ_V2_TEST_REAL_NOTIFY = Join-Path $Scripts "notify_hermes.py"
$env:CARDZ_V2_TEST_NOTIFY_STATE_DIR = Join-Path $tmp "notify-mutation-state"
$env:CARDZ_V2_TEST_NOTIFY_RECEIPT = Join-Path $tmp "notify-mutation-called.txt"
$env:CARDZ_V2_TEST_POPUP_RECEIPT = Join-Path $tmp "popup-mutation-called.txt"
$rMutation = Invoke-Capture $PsExe "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$mutationLauncher`" -SelfTest"
Remove-Item env:CARDZ_V2_LAUNCHER_LOG_DIR -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_LAUNCHER_PROVENANCE_DIR -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_WSL_SELFHEAL_PS1 -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_NOTIFY_HERMES_PY -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_LAUNCHER_MSG_EXE -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_TEST_REAL_NOTIFY -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_TEST_NOTIFY_STATE_DIR -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_TEST_NOTIFY_RECEIPT -ErrorAction SilentlyContinue
Remove-Item env:CARDZ_V2_TEST_POPUP_RECEIPT -ErrorAction SilentlyContinue
$mutationLogFiles = @(Get-ChildItem -Path $mutationLogDir -Filter "launcher-*.log" -ErrorAction SilentlyContinue)
$mutationLog = if ($mutationLogFiles.Count -eq 1) { Get-Content -LiteralPath $mutationLogFiles[0].FullName -Raw } else { "" }
Check "launcher-wsl-dead-mutation-caught" (($rMutation.Code -ne 3) -and (-not ($mutationLog -match "CARDZ_V2_LAUNCHER_ALERT_NOTIFY key=v2-launcher-wsl-dead"))) "removing the guard loses the required exit and notify evidence"

# ---- (c) watchdog health.json rules (contract C1) --------------------------
$wdState = Join-Path $tmp "wd-state"
$wdLog = Join-Path $tmp "wd-log"
New-Item -ItemType Directory -Force -Path $wdState, $wdLog | Out-Null

function Write-Health([string]$name, [string]$writtenAtUtc, [string]$runState, [string]$tickPhase, [string[]]$parked, [string]$businessDate = "2026-08-22") {
    $path = Join-Path $tmp ("health-" + $name + ".json")
    $doc = [ordered]@{
        schema = 1
        written_at_utc = $writtenAtUtc
        written_at_jst = $writtenAtUtc
        business_date = $businessDate
        run_state = $runState
        tick_phase = $tickPhase
        tick_exit_code = 0
        parked = @($parked)
        autonomous_proven = $true
    }
    ($doc | ConvertTo-Json -Depth 4) | Out-File -FilePath $path -Encoding utf8
    return $path
}

function Invoke-Watchdog([string]$healthPath, [string]$nowUtc) {
    $a = "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Watchdog`"" +
         " -NotifyDryRun -SkipLiveCheck -SkipLogCheck" +
         " -StateDir `"$wdState`" -LogDir `"$wdLog`" -HealthPath `"$healthPath`" -NowUtc $nowUtc"
    return (Invoke-Capture $PsExe $a)
}

# JST = UTC+9. 03:00Z = 12:00 JST (inside window). 09:00Z = 18:00 JST (after window).
$inWindowNow = "2026-08-22T03:00:00Z"
$afterWindowNow = "2026-08-22T09:00:00Z"

$hStale = Write-Health "stale" "2026-08-22T02:00:00Z" "RUNNING" "started" @()
$r1 = Invoke-Watchdog $hStale $inWindowNow
Check "watchdog-stale-fires" (($r1.Out -match "NOTIFY_DRYRUN v2-health-stale") -and ($r1.Code -eq 2)) "exit=$($r1.Code) :: $((($r1.Out -split "`r?`n") | Where-Object { $_ -match 'NOTIFY_DRYRUN' } | Select-Object -First 1))"

$hFresh = Write-Health "fresh" "2026-08-22T02:55:00Z" "RUNNING" "ended" @()
$r2 = Invoke-Watchdog $hFresh $inWindowNow
Check "watchdog-fresh-silent" (($r2.Code -eq 0) -and (-not ($r2.Out -match "NOTIFY_DRYRUN"))) "exit=$($r2.Code) :: $((($r2.Out -split "`r?`n") | Where-Object { $_ -match 'OK CARDZ watchdog' } | Select-Object -First 1))"

$hMissing = Join-Path $tmp "health-does-not-exist.json"
$r3 = Invoke-Watchdog $hMissing $inWindowNow
Check "watchdog-missing-fires" (($r3.Out -match "NOTIFY_DRYRUN v2-health-missing") -and ($r3.Code -eq 2)) "exit=$($r3.Code) :: $((($r3.Out -split "`r?`n") | Where-Object { $_ -match 'NOTIFY_DRYRUN' } | Select-Object -First 1))"

$hParked = Write-Health "parked" "2026-08-22T02:55:00Z" "RUNNING" "ended" @("pc_lane", "snk_lane")
$r4 = Invoke-Watchdog $hParked $inWindowNow
Check "watchdog-parked-fires" (($r4.Out -match "NOTIFY_DRYRUN v2-parked") -and ($r4.Code -eq 2)) "exit=$($r4.Code) :: $((($r4.Out -split "`r?`n") | Where-Object { $_ -match 'NOTIFY_DRYRUN' } | Select-Object -First 1))"
Check "watchdog-parked-shows-unpark" ($r4.Out -match "daily_chain_v2\.py unpark --task pc_lane") "unpark command is in the alert text"

$hLate = Write-Health "late" "2026-08-22T08:55:00Z" "RUNNING" "ended" @()
$r5 = Invoke-Watchdog $hLate $afterWindowNow
Check "watchdog-not-done-fires" (($r5.Out -match "NOTIFY_DRYRUN v2-not-done") -and ($r5.Code -eq 2)) "exit=$($r5.Code) :: $((($r5.Out -split "`r?`n") | Where-Object { $_ -match 'NOTIFY_DRYRUN' } | Select-Object -First 1))"

$hDone = Write-Health "done" "2026-08-22T08:55:00Z" "PUBLISHED" "ended" @()
$r6 = Invoke-Watchdog $hDone $afterWindowNow
Check "watchdog-after-window-done-silent" (($r6.Code -eq 0) -and (-not ($r6.Out -match "NOTIFY_DRYRUN"))) "exit=$($r6.Code) :: $((($r6.Out -split "`r?`n") | Where-Object { $_ -match 'OK CARDZ watchdog' } | Select-Object -First 1))"

# Yesterday's PUBLISHED health.json (business_date 08-21) must NOT count as
# today's DONE: inside the window it is simply stale, after 17:30 it is not-done.
$hYest = Write-Health "yesterday-done" "2026-08-21T08:55:00Z" "PUBLISHED" "ended" @() "2026-08-21"
$r7 = Invoke-Watchdog $hYest $inWindowNow
Check "watchdog-yesterday-done-stale-fires" (($r7.Out -match "NOTIFY_DRYRUN v2-health-stale") -and ($r7.Code -eq 2)) "exit=$($r7.Code) :: $((($r7.Out -split "`r?`n") | Where-Object { $_ -match 'NOTIFY_DRYRUN' } | Select-Object -First 1))"
$r8 = Invoke-Watchdog $hYest $afterWindowNow
Check "watchdog-yesterday-done-not-done-fires" (($r8.Out -match "NOTIFY_DRYRUN v2-not-done") -and ($r8.Code -eq 2)) "exit=$($r8.Code) :: $((($r8.Out -split "`r?`n") | Where-Object { $_ -match 'NOTIFY_DRYRUN' } | Select-Object -First 1))"

# ---- (d) installer -Print: all three tasks go through the vbs --------------
$rInst = Invoke-Capture $PsExe "-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Installer`" -Print"
$wscriptHits = ([regex]::Matches($rInst.Out, "wscript\.exe")).Count
Check "installer-print-exit0" ($rInst.Code -eq 0) "exit=$($rInst.Code)"
Check "installer-print-three-wscript-actions" ($wscriptHits -ge 3) "wscript.exe occurrences = $wscriptHits"
Check "installer-print-daily-action" ($rInst.Out -match "cardz_daily_v2_launcher\.ps1") "daily launcher action present"
Check "installer-print-watchdog-action" ($rInst.Out -match "watchdog_live_release\.ps1") "watchdog action present"
Check "installer-print-promo-action" ($rInst.Out -match "promo_after_publish\.py") "promo action present"
Check "installer-print-no-apply" (-not ($rInst.Out -match '"result"')) "-Print must not apply"
# P0 2026-08-24: the computed StartBoundary used to be invisible in the plan, which
# is why a mid-window -Apply could delete 79 of the day's 82 ticks in silence.
Check "installer-print-shows-start-boundary" ($rInst.Out -match '"startBoundary"') "plan must expose the computed StartBoundary"
Check "installer-print-shows-window-state" ($rInst.Out -match '"insideDailyWindow"') "plan must expose whether we are re-installing mid-window"

Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "RESULT fails=$fails"
if ($fails -gt 0) { exit 1 }
exit 0
