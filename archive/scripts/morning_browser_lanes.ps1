# CARDZ 037 morning chain: PC full-page cap first, then bounded HTTP, then publish.
# Registered as Task Scheduler job CARDZ-037-Morning-Browser-Lanes.
#
# 2026-08-20: HTTP incr started with GemRate (child timeout 16040s > task PT4H).
# Task Scheduler killed the job; 9333 never captured PriceCharting pages;
# live stayed on yesterday's bake. PC K-line observed_date on the 1st of the
# month is the source field — still capture the whole page every morning.
#
# Order is load-bearing (test_pc_lane_full_sweep.py):
#   CDP 9333 -> browser incr --force-network -> SNK catch-up (no GemRate) -> accept/publish
# GemRate lives on the nightly chain. collect_control runs GemRate BEFORE SNK,
# so an HTTP-all incr here would eat the HTTP cap and skip SNK again.
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
. (Join-Path $PSScriptRoot "cardz_chain_lib.ps1")

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
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "preflight_daily_chain.ps1") -Mode morning *>> $log
    $preflightExit = $LASTEXITCODE
    if ($preflightExit -eq 1) {
        "[$stamp] preflight HARD fail; morning chain aborted" | Tee-Object -FilePath $log -Append
        Copy-Item -Force $log $crashLog -ErrorAction SilentlyContinue
        exit 1
    }

    $idle = Wait-CardzChainIdle -WaitSeconds 600 -ExcludeTaskNames @("CARDZ-037-Morning-Browser-Lanes") -LogPath $log
    if (-not $idle.Idle) {
        "[$stamp] sibling still running $($idle.Busy -join ','); morning continues so PC cap is not skipped" | Tee-Object -FilePath $log -Append
    }

    # PC first. Collect red still must not block accept/publish, but the page
    # capture has to happen before any HTTP adapter can burn the slot.
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ensure_chrome_cdp.ps1") -Port 9333 *>> $log
    $cdpExit = $LASTEXITCODE

    $browserExit = -1
    $discoverExit = -1
    $boxPcExit = -1
    if ($cdpExit -eq 0) {
        "[$stamp] PC full-page cap starting --force-network" | Tee-Object -FilePath $log -Append
        $browserExit = Invoke-CappedProcess -File $py -Arguments @(
            "-X", "utf8", "-u", "pipelines\collect_control.py", "incr",
            "--adapter", "browser", "--ensure-browser", "--force-network"
        ) -Seconds 5400 -WorkingDirectory $repo -LogPath $log
        & $py -X utf8 -u "pipelines\operator_control.py" daily-discover-activate --lane browser *>> $log
        $discoverExit = $LASTEXITCODE
        & $py -X utf8 -u "pipelines\sealed_daily.py" collect --adapter sealed_pc *>> $log
        $boxPcExit = $LASTEXITCODE
    } else {
        "[$stamp] ensure_chrome_cdp failed exit=$cdpExit; browser lanes skipped, SNK catch-up + accept still run" | Tee-Object -FilePath $log -Append
    }

    if ($discoverExit -ne 0) {
        "[$stamp] discovery exit=$discoverExit; daily-accept still runs on the current universe" | Tee-Object -FilePath $log -Append
    }

    $httpExit = Invoke-CappedProcess -File $py -Arguments @(
        "-X", "utf8", "-u", "pipelines\collect_control.py", "incr",
        "--adapter", "snk_trades", "--adapter", "snk_price", "--adapter", "snk_en_image"
    ) -Seconds 2400 -WorkingDirectory $repo -LogPath $log
    $collectExit = 0
    if ($httpExit -ne 0 -or $browserExit -ne 0) { $collectExit = 1 }

    & $py -X utf8 -u "pipelines\collect_control.py" first-stock --adapter http *>> $log
    $firstHttpExit = $LASTEXITCODE
    if ($cdpExit -eq 0) {
        & $py -X utf8 -u "pipelines\collect_control.py" first-stock --adapter browser --ensure-browser --force-network *>> $log
        $firstBrowserExit = $LASTEXITCODE
    } else {
        $firstBrowserExit = -1
    }
    if ($firstHttpExit -ne 0 -or $firstBrowserExit -ne 0) {
        "[$stamp] first-stock http=$firstHttpExit browser=$firstBrowserExit; daily-accept still runs" | Tee-Object -FilePath $log -Append
    }

    & $py -X utf8 -u "pipelines\fx_rates.py" --timeout-seconds 40 *>> $log
    $fxExit = $LASTEXITCODE
    if ($fxExit -eq 0) {
        & $py -X utf8 -u "pipelines\fx_db_load.py" *>> $log
        $fxExit = $LASTEXITCODE
    }
    if ($fxExit -ne 0) {
        "[$stamp] fx collect/load exit=$fxExit; daily-accept still runs with the previous DB rates" | Tee-Object -FilePath $log -Append
    }

    & $py -X utf8 -u "pipelines\operator_control.py" daily-accept *>> $log
    $acceptExit = $LASTEXITCODE

    & $py -X utf8 -u "pipelines\sealed_daily.py" compose *>> $log
    $boxComposeExit = $LASTEXITCODE
    & $py -X utf8 -u "pipelines\sealed_daily.py" export --output "data\public\box-subset.json" *>> $log
    $boxExportExit = $LASTEXITCODE
    if ($boxExportExit -ne 0) {
        "[$stamp] BOX export failed exit=$boxExportExit; release will publish the previous sidecar" | Tee-Object -FilePath $log -Append
    }

    if ($acceptExit -eq 0) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "daily_public_release.ps1") @releaseArgs *>> $log
        $publishExit = $LASTEXITCODE
    } else {
        "[$stamp] daily-accept failed exit=$acceptExit; public release skipped" | Tee-Object -FilePath $log -Append
        $publishExit = -1
    }

    $done = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
    "[$done] morning browser chain done cdp=$cdpExit http=$httpExit collect=$collectExit discover=$discoverExit fx=$fxExit accept=$acceptExit publish=$publishExit boxPc=$boxPcExit boxCompose=$boxComposeExit boxExport=$boxExportExit" | Tee-Object -FilePath $log -Append
    Copy-Item -Force $log $crashLog -ErrorAction SilentlyContinue
    $chainExit = 0
    if ($acceptExit -ne 0 -or $publishExit -ne 0) { $chainExit = 1 }
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
