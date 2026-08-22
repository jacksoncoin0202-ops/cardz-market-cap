# CARDZ 037 refresh+publish slot: daily-accept -> public release.
# Registered as Task Scheduler job CARDZ-037-Refresh-Publish (11:30 / 16:30 JST).
# ExecutionTimeLimit is PT1H on purpose. If accept+publish cannot finish in an
# hour the job is stuck (usually a silent daily-accept hang), not "needs more
# time". Fail the slot and let the next one retry. Do not raise the limit.
#
# 冇 collector 喺呢度 —— 呢個 slot 淨係重試，唔係第二次更新。
# owner 要一日更新一次：09:30 朝鏈（收貨 → re-rank → 發佈）就係嗰一次。
# 呢個 slot 11:30 / 16:30 再行一次 accept + publish，用嚟救朝鏈死咗嗰日：
#   - 朝鏈 accept 死咗 → 呢度 accept 返，個站當日仍然更新到
#   - 朝鏈 publish 死咗 → 呢度推返
# 兩個 slot 之間冇 collector 行過，所以 DB 冇新嘢，accept 出返同一個 ranking sha，
# daily_public_release 個 no-change 閘就會 fire，零 commit 零部署（實測 106 秒收工）。
# 即係朝鏈正常嗰日，呢兩個 slot 唔會令個站更新多過一次。
param([switch]$Scheduled)
$ErrorActionPreference = "Continue"
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$env:CARDZ_DAILY_CHAIN = "1"
$repo = Split-Path -Parent $PSScriptRoot
$py = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
$logDir = Join-Path $repo "data\runtime\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$log = Join-Path $logDir "refresh-$stamp.log"
. (Join-Path $PSScriptRoot "cardz_chain_lib.ps1")

Set-Location $repo
"[$stamp] refresh+publish slot start" | Tee-Object -FilePath $log -Append
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
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "preflight_daily_chain.ps1") -Mode refresh *>> $log
$preflightExit = $LASTEXITCODE
if ($preflightExit -eq 1) {
    "[$stamp] preflight HARD fail; refresh slot aborted" | Tee-Object -FilePath $log -Append
    exit 1
}

$idle = Wait-CardzChainIdle -WaitSeconds 1500 -ExcludeTaskNames @("CARDZ-037-Refresh-Publish") -LogPath $log
if (-not $idle.Idle) {
    "[$stamp] SKIPPED_BUSY sibling=$($idle.Busy -join ','); next refresh slot retries. exit=0" | Tee-Object -FilePath $log -Append
    & $py -X utf8 -u "scripts\notify_hermes.py" chain --chain refresh --status "SKIPPED_BUSY $($idle.Busy -join ',')" --exit-code 0 --log $log --notify-on failure *>> $log
    exit 0
}

# 朝鏈 accept 死喺「新卡未 first-stock」嗰日，呢個 slot 要自己補 checkpoint，
# 唔可以淨係再撞同一道閘（2026-08-17 v2016）。residual stock 仍然唔拉。
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ensure_chrome_cdp.ps1") -Port 9333 *>> $log
$cdpExit = $LASTEXITCODE
& $py -X utf8 -u "pipelines\collect_control.py" first-stock --adapter http *>> $log
$firstHttpExit = $LASTEXITCODE
if ($cdpExit -eq 0) {
    & $py -X utf8 -u "pipelines\collect_control.py" first-stock --adapter browser --ensure-browser *>> $log
    $firstBrowserExit = $LASTEXITCODE
} else {
    "[$stamp] ensure_chrome_cdp failed exit=$cdpExit; browser first-stock skipped" | Tee-Object -FilePath $log -Append
    $firstBrowserExit = -1
}
"[$stamp] first-stock http=$firstHttpExit browser=$firstBrowserExit cdp=$cdpExit" | Tee-Object -FilePath $log -Append

# FX（2026-08-17）：同朝鏈一樣先收 31 隻匯率入 DB（朝鏈 FX 死咗呢度補返）；紅唔擋 accept。
$fxExit = Invoke-CappedProcess -File $py -Arguments @("-X", "utf8", "-u", "pipelines\fx_rates.py", "--timeout-seconds", "40") -Seconds 180 -WorkingDirectory $repo -LogPath $log
if ($fxExit -eq 0) { $fxExit = Invoke-CappedProcess -File $py -Arguments @("-X", "utf8", "-u", "pipelines\fx_db_load.py") -Seconds 120 -WorkingDirectory $repo -LogPath $log }
if ($fxExit -ne 0) { "[$stamp] fx collect/load exit=$fxExit; daily-accept still runs with the previous DB rates" | Tee-Object -FilePath $log -Append }

# Accept should finish in minutes. Sitting the full PT1H means it is stuck
# (usually waiting on another writer). Fail at 10 minutes and retry twice.
$acceptExit = 1
for ($attempt = 1; $attempt -le 3; $attempt++) {
    "[$stamp] daily-accept attempt $attempt/3 (timeout 600s)" | Tee-Object -FilePath $log -Append
    $acceptExit = Invoke-CappedProcess -File $py -Arguments @("-X", "utf8", "-u", "pipelines\operator_control.py", "daily-accept") -Seconds 600 -WorkingDirectory $repo -LogPath $log
    if ($acceptExit -eq 0) { break }
    "[$stamp] daily-accept attempt $attempt failed exit=$acceptExit" | Tee-Object -FilePath $log -Append
    if ($attempt -lt 3) { Start-Sleep -Seconds 20 }
}

# daily-accept 紅就唔發佈：ranking generation 可能寫到一半。
if ($acceptExit -eq 0) {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "daily_public_release.ps1") @releaseArgs *>> $log
    $publishExit = $LASTEXITCODE
} else {
    "[$stamp] daily-accept failed exit=$acceptExit; public release skipped" | Tee-Object -FilePath $log -Append
    $publishExit = -1
}

$done = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
"[$done] refresh+publish slot done fx=$fxExit accept=$acceptExit publish=$publishExit" | Tee-Object -FilePath $log -Append
$chainExit = 0
if ($acceptExit -ne 0 -or $publishExit -ne 0) { $chainExit = 1 }
& $py -X utf8 -u "scripts\notify_hermes.py" chain --chain refresh --status "accept=$acceptExit publish=$publishExit" --exit-code $chainExit --log $log --notify-on failure *>> $log
exit $chainExit
