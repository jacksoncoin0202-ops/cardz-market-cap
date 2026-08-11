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

# `--adapter http` = ADAPTER_LANE 入面標住 "http" 嗰批，唔再喺呢度抄名單。
# 舊版逐個名寫死，漏咗 snk_en_image：佢一樣係純 HTTP，但夜鏈朝鏈都冇佢，
# 由註冊嗰日起冇任何 scheduled task 收過，實測 stale 99 小時。
& $py -X utf8 -u "pipelines\collect_control.py" incr --adapter http *>> $log
$collectExit = $LASTEXITCODE

# 一條 lane 收唔到貨唔應該連 re-rank 都跳過 —— DB 入面舊 observation 仍然行得，
# daily-accept 自己有 gate。收集紅照樣喺 exit code 報返出嚟，唔會當成功。
& $py -X utf8 -u "pipelines\operator_control.py" daily-accept *>> $log
$acceptExit = $LASTEXITCODE

$done = (Get-Date).ToUniversalTime().ToString("yyyyMMdd'T'HHmmss'Z'")
"[$done] nightly chain done collect=$collectExit accept=$acceptExit" | Tee-Object -FilePath $log -Append
if ($collectExit -ne 0 -or $acceptExit -ne 0) { exit 1 }
exit 0
