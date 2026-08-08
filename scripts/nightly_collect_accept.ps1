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

# Accept whatever evidence landed even when a collector lane failed:
# §3.11b bumps accepted_at on identical content, so the FE generation
# still flips and the failure stays visible in this log + exit code.
& $py -X utf8 -u "pipelines\operator_control.py" daily-accept *>> $log
$acceptExit = $LASTEXITCODE

$done = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
"[$done] nightly chain done collect=$collectExit accept=$acceptExit" | Tee-Object -FilePath $log -Append
if (($collectExit -ne 0) -or ($acceptExit -ne 0)) { exit 1 }
exit 0
