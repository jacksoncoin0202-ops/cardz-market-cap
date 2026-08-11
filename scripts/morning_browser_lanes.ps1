# CARDZ 036 morning chain: browser-dependent collectors -> daily-accept re-rank.
# Registered as Task Scheduler job CARDZ-036-Morning-Browser-Lanes.
# These lanes (pc_ebay_sales, en_price_ref) need a headed Chrome CDP session,
# so they run in the morning slot where ensure_chrome_cdp can own the desktop;
# the HTTP-only lanes live in nightly_collect_accept.ps1.
$ErrorActionPreference = "Continue"
# Windows PowerShell 5.1 redirects as UTF-16LE by default; force UTF-8 so the
# morning log stays greppable from every tool on the box.
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$repo = Split-Path -Parent $PSScriptRoot
$py = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
$logDir = Join-Path $repo "data\runtime\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$log = Join-Path $logDir "morning-$stamp.log"

Set-Location $repo
"[$stamp] morning browser chain start" | Tee-Object -FilePath $log -Append

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "ensure_chrome_cdp.ps1") -Port 9333 *>> $log
$cdpExit = $LASTEXITCODE
if ($cdpExit -ne 0) {
    "[$stamp] ensure_chrome_cdp failed exit=$cdpExit; aborting browser lanes" | Tee-Object -FilePath $log -Append
    exit 1
}

& $py -X utf8 -u "pipelines\collect_control.py" incr --adapter en_price_ref --adapter pc_ebay_sales --ensure-browser *>> $log
$collectExit = $LASTEXITCODE
if ($collectExit -ne 0) {
    "[$stamp] morning collect failed exit=$collectExit; daily-accept not run" | Tee-Object -FilePath $log -Append
    exit 1
}

& $py -X utf8 -u "pipelines\operator_control.py" daily-accept *>> $log
$acceptExit = $LASTEXITCODE
if ($acceptExit -ne 0) {
    "[$stamp] morning daily-accept failed exit=$acceptExit; public release not run" | Tee-Object -FilePath $log -Append
    exit 1
}

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "daily_public_release.ps1") *>> $log
$publishExit = $LASTEXITCODE
if ($publishExit -ne 0) {
    "[$stamp] morning public release failed exit=$publishExit" | Tee-Object -FilePath $log -Append
    exit 1
}

$done = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
"[$done] morning browser chain done cdp=$cdpExit collect=$collectExit accept=$acceptExit publish=$publishExit" | Tee-Object -FilePath $log -Append
exit 0
