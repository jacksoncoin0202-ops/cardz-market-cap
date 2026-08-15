# CARDZ 037 morning chain: due collectors -> daily-accept re-rank.
# Registered as Task Scheduler job CARDZ-037-Morning-Browser-Lanes.
# Browser lanes need headed Chrome :9333. HTTP lanes do not — but they still
# have to run here. Nightly 03:30 skips anything younger than REFRESH_DUE_HOURS
# (12h). A 19:00 JST catch-up is still "ok" at 03:30, then due at 09:30 with
# nobody collecting until the next night. 2026-08-15: GemRate 1368 + SNK
# 701/700 sat due all day. HTTP incr runs even if CDP is down.
param([switch]$Scheduled)
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$env:CARDZ_DAILY_CHAIN = "1"
$repo = Split-Path -Parent $PSScriptRoot
$py = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
$logDir = Join-Path $repo "data\runtime\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$log = Join-Path $logDir "morning-$stamp.log"
$crashLog = Join-Path $env:TEMP "cardz-036-morning-last.log"

Set-Location $repo
try {
    "[$stamp] morning browser chain start" | Tee-Object -FilePath $log -Append
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
    $releaseArgs = @(); if ($isScheduled) { $releaseArgs += "-Scheduled" }

    # 收唔到貨 ≠ 出街數據壞。舊版一係 CDP 起唔到、一係 discover/e2e S0 abort，
    # 就連 daily-accept 同發佈都唔行。2026-08-13 朝鏈就係咁：pending activation
    # 觸發 036 e2e，S0 見到呢條 task 自己 Running，然後跳過出街。
    # collect / discover 紅照記，accept + publish 仍然要行。
    & $py -X utf8 -u "pipelines\collect_control.py" incr --adapter http *>> $log
    $httpExit = $LASTEXITCODE

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ensure_chrome_cdp.ps1") -Port 9333 *>> $log
    $cdpExit = $LASTEXITCODE

    if ($cdpExit -eq 0) {
        & $py -X utf8 -u "pipelines\collect_control.py" incr --adapter browser --ensure-browser *>> $log
        $browserExit = $LASTEXITCODE
        & $py -X utf8 -u "pipelines\operator_control.py" daily-discover-activate --lane browser *>> $log
        $discoverExit = $LASTEXITCODE
    } else {
        "[$stamp] ensure_chrome_cdp failed exit=$cdpExit; browser lanes skipped, HTTP already ran, accept still runs" | Tee-Object -FilePath $log -Append
        $browserExit = -1
        $discoverExit = -1
    }
    $collectExit = 0
    if ($httpExit -ne 0 -or $browserExit -ne 0) { $collectExit = 1 }

    if ($discoverExit -ne 0) {
        "[$stamp] discovery exit=$discoverExit; daily-accept still runs on the current universe" | Tee-Object -FilePath $log -Append
    }

    & $py -X utf8 -u "pipelines\operator_control.py" daily-accept *>> $log
    $acceptExit = $LASTEXITCODE

    if ($acceptExit -eq 0) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "daily_public_release.ps1") @releaseArgs *>> $log
        $publishExit = $LASTEXITCODE
    } else {
        "[$stamp] daily-accept failed exit=$acceptExit; public release skipped" | Tee-Object -FilePath $log -Append
        $publishExit = -1
    }

    $done = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
    "[$done] morning browser chain done cdp=$cdpExit http=$httpExit collect=$collectExit discover=$discoverExit accept=$acceptExit publish=$publishExit" | Tee-Object -FilePath $log -Append
    Copy-Item -Force $log $crashLog -ErrorAction SilentlyContinue
    $chainExit = 0
    if ($cdpExit -ne 0 -or $collectExit -ne 0 -or $discoverExit -ne 0 -or $acceptExit -ne 0 -or $publishExit -ne 0) { $chainExit = 1 }
    & $py -X utf8 -u "scripts\notify_hermes.py" chain --chain morning --status "cdp=$cdpExit http=$httpExit browser=$browserExit discover=$discoverExit accept=$acceptExit publish=$publishExit" --exit-code $chainExit --log $log --notify-on always *>> $log
    & $py -X utf8 -u "scripts\notify_hermes.py" digest *>> $log
    exit $chainExit
} catch {
    $msg = "[$stamp] morning chain CRASH: $($_.Exception.Message)"
    $msg | Tee-Object -FilePath $log -Append
    $msg | Out-File -FilePath $crashLog -Append
    & $py -X utf8 -u "scripts\notify_hermes.py" chain --chain morning --status "CRASH (see log)" --exit-code 1 --log $log --notify-on always *>> $log
    exit 1
}
