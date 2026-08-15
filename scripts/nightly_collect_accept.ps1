# CARDZ 036 nightly chain: HTTP collectors -> daily-accept re-rank.
# Registered as Task Scheduler job CARDZ-036-Nightly-Collect-Accept.
# Browser-dependent lanes (pc_ebay_sales, en_price_ref) are deliberately
# NOT here: they need a headed Chrome session and run in the morning slot.
param([switch]$Scheduled)
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$env:CARDZ_DAILY_CHAIN = "1"
$repo = Split-Path -Parent $PSScriptRoot
$py = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
$logDir = Join-Path $repo "data\runtime\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$log = Join-Path $logDir "nightly-$stamp.log"
$crashLog = Join-Path $env:TEMP "cardz-036-nightly-last.log"

Set-Location $repo
try {
    "[$stamp] nightly chain start" | Tee-Object -FilePath $log -Append
    Copy-Item -Force $log $crashLog -ErrorAction SilentlyContinue
    function Test-LaunchedByTaskScheduler {
        try {
            $schedulePid = [int](Get-CimInstance Win32_Service -Filter "Name='Schedule'" -ErrorAction Stop).ProcessId
            if ($schedulePid -le 0) { return $false }
            $me = Get-CimInstance Win32_Process -Filter "ProcessId=$PID" -ErrorAction Stop
            return ([int]$me.ParentProcessId -eq $schedulePid)
        } catch { return $false }
    }
    $launchedByTS = Test-LaunchedByTaskScheduler
    $isScheduled = $Scheduled.IsPresent -or $launchedByTS
    $launcher = if ($launchedByTS) { "task-scheduler" } elseif ($Scheduled) { "switch-override" } else { "manual" }
    "[$stamp] launcher=$launcher scheduled=$isScheduled" | Tee-Object -FilePath $log -Append
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "preflight_daily_chain.ps1") -Mode nightly *>> $log
    $preflightExit = $LASTEXITCODE
    if ($preflightExit -eq 1) {
        "[$stamp] preflight HARD fail; nightly chain aborted" | Tee-Object -FilePath $log -Append
        Copy-Item -Force $log $crashLog -ErrorAction SilentlyContinue
        exit 1
    }

    & $py -X utf8 -u "pipelines\collect_control.py" incr --adapter http *>> $log
    $collectExit = $LASTEXITCODE

    & $py -X utf8 -u "pipelines\operator_control.py" daily-discover-activate --lane http *>> $log
    $discoverExit = $LASTEXITCODE

    if ($discoverExit -ne 0) {
        "[$stamp] discovery failed exit=$discoverExit; daily-accept still runs on the current universe" | Tee-Object -FilePath $log -Append
    }

    & $py -X utf8 -u "pipelines\operator_control.py" daily-accept *>> $log
    $acceptExit = $LASTEXITCODE

    $done = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
    "[$done] nightly chain done collect=$collectExit discover=$discoverExit accept=$acceptExit" | Tee-Object -FilePath $log -Append
    Copy-Item -Force $log $crashLog -ErrorAction SilentlyContinue
    $chainExit = 0
    if ($collectExit -ne 0 -or $discoverExit -ne 0 -or $acceptExit -ne 0) { $chainExit = 1 }
    & $py -X utf8 -u "scripts\notify_hermes.py" chain --chain nightly --status "collect=$collectExit discover=$discoverExit accept=$acceptExit" --exit-code $chainExit --log $log --notify-on failure *>> $log
    exit $chainExit
} catch {
    $msg = "[$stamp] nightly chain CRASH: $($_.Exception.Message)"
    $msg | Tee-Object -FilePath $log -Append
    $msg | Out-File -FilePath $crashLog -Append
    & $py -X utf8 -u "scripts\notify_hermes.py" chain --chain nightly --status "CRASH (see log)" --exit-code 1 --log $log --notify-on failure *>> $log
    exit 1
}
