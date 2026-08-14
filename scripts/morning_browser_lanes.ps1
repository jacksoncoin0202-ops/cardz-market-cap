# CARDZ 036 morning chain: browser-dependent collectors -> daily-accept re-rank.
# Registered as Task Scheduler job CARDZ-036-Morning-Browser-Lanes.
# These lanes (pc_ebay_sales, en_price_ref) need a headed Chrome CDP session,
# so they run in the morning slot where ensure_chrome_cdp can own the desktop;
# the HTTP-only lanes live in nightly_collect_accept.ps1.
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

    # 收唔到貨 ≠ 出街數據壞。舊版一係 CDP 起唔到、一係 discover/e2e S0 abort，
    # 就連 daily-accept 同發佈都唔行。2026-08-13 朝鏈就係咁：pending activation
    # 觸發 036 e2e，S0 見到呢條 task 自己 Running，然後跳過出街。
    # collect / discover 紅照記，accept + publish 仍然要行。
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ensure_chrome_cdp.ps1") -Port 9333 *>> $log
    $cdpExit = $LASTEXITCODE

    if ($cdpExit -eq 0) {
        & $py -X utf8 -u "pipelines\collect_control.py" incr --adapter browser --ensure-browser *>> $log
        $collectExit = $LASTEXITCODE
        & $py -X utf8 -u "pipelines\operator_control.py" daily-discover-activate --lane browser *>> $log
        $discoverExit = $LASTEXITCODE
    } else {
        "[$stamp] ensure_chrome_cdp failed exit=$cdpExit; browser lanes skipped, accept still runs" | Tee-Object -FilePath $log -Append
        $collectExit = -1
        $discoverExit = -1
    }

    if ($discoverExit -ne 0) {
        "[$stamp] discovery exit=$discoverExit; daily-accept still runs on the current universe" | Tee-Object -FilePath $log -Append
    }

    & $py -X utf8 -u "pipelines\operator_control.py" daily-accept *>> $log
    $acceptExit = $LASTEXITCODE

    if ($acceptExit -eq 0) {
        & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "daily_public_release.ps1") *>> $log
        $publishExit = $LASTEXITCODE
    } else {
        "[$stamp] daily-accept failed exit=$acceptExit; public release skipped" | Tee-Object -FilePath $log -Append
        $publishExit = -1
    }

    $done = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
    "[$done] morning browser chain done cdp=$cdpExit collect=$collectExit discover=$discoverExit accept=$acceptExit publish=$publishExit" | Tee-Object -FilePath $log -Append
    Copy-Item -Force $log $crashLog -ErrorAction SilentlyContinue
    if ($cdpExit -ne 0 -or $collectExit -ne 0 -or $discoverExit -ne 0 -or $acceptExit -ne 0 -or $publishExit -ne 0) { exit 1 }
    exit 0
} catch {
    $msg = "[$stamp] morning chain CRASH: $($_.Exception.Message)"
    $msg | Tee-Object -FilePath $log -Append
    $msg | Out-File -FilePath $crashLog -Append
    exit 1
}
