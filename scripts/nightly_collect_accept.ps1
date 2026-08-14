# CARDZ 036 nightly chain: HTTP collectors -> daily-accept re-rank.
# Registered as Task Scheduler job CARDZ-036-Nightly-Collect-Accept.
# Browser-dependent lanes (pc_ebay_sales, en_price_ref) are deliberately
# NOT here: they need a headed Chrome session and run in the morning slot.
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
    if ($collectExit -ne 0 -or $discoverExit -ne 0 -or $acceptExit -ne 0) { exit 1 }
    exit 0
} catch {
    $msg = "[$stamp] nightly chain CRASH: $($_.Exception.Message)"
    $msg | Tee-Object -FilePath $log -Append
    $msg | Out-File -FilePath $crashLog -Append
    exit 1
}
