# CARDZ 036 nightly chain: HTTP collectors -> daily-accept re-rank.
# Registered as Task Scheduler job CARDZ-036-Nightly-Collect-Accept.
# Browser-dependent lanes (pc_ebay_sales, en_price_ref) are deliberately
# NOT here: they need a headed Chrome session and run in the morning slot.
$ErrorActionPreference = "Continue"
# Windows PowerShell 5.1 redirects as UTF-16LE by default; force UTF-8 so the
# nightly log stays greppable from every tool on the box.
$PSDefaultParameterValues['Out-File:Encoding'] = 'utf8'
$repo = Split-Path -Parent $PSScriptRoot
$py = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
$logDir = Join-Path $repo "data\runtime\logs"
New-Item -ItemType Directory -Force $logDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
$log = Join-Path $logDir "nightly-$stamp.log"

Set-Location $repo
"[$stamp] nightly chain start" | Tee-Object -FilePath $log -Append

& $py -X utf8 -u "pipelines\collect_control.py" incr --adapter snk_trades --adapter snk_price --adapter gemrate_pop *>> $log
$collectExit = $LASTEXITCODE
if ($collectExit -ne 0) {
    "[$stamp] nightly collect failed exit=$collectExit; daily-accept not run" | Tee-Object -FilePath $log -Append
    exit 1
}

& $py -X utf8 -u "pipelines\operator_control.py" daily-accept *>> $log
$acceptExit = $LASTEXITCODE
if ($acceptExit -ne 0) {
    "[$stamp] nightly daily-accept failed exit=$acceptExit" | Tee-Object -FilePath $log -Append
    exit 1
}

$done = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
"[$done] nightly chain done collect=$collectExit accept=$acceptExit" | Tee-Object -FilePath $log -Append
exit 0
